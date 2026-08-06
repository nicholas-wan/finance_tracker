"""Conservative, explainable card-transaction anomaly checks.

These checks surface transactions for human review; they do not label fraud.
Refunds are netted before duplicate and amount checks so reversals do not create
false alarms. Every signal uses fields printed on the statement or stable
history derived from earlier statements.
"""

from collections import defaultdict
from statistics import median


EXCLUDED_CATEGORIES = {"Payment", "Rebates", "Fees & charges", "Insurance"}
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


def _stronger(current, candidate):
    if SEVERITY_RANK[candidate] > SEVERITY_RANK[current]:
        return candidate
    return current


def detect_risks(transactions, merchant_key, recognized_ids=None):
    """Annotate representative transactions and return unresolved review totals."""
    recognized_ids = set(recognized_ids or [])
    groups = defaultdict(list)

    for transaction in transactions:
        if transaction.get("category") in EXCLUDED_CATEGORIES:
            continue
        groups[(
            transaction.get("date") or transaction.get("month"),
            transaction.get("card", ""),
            transaction["description"].upper().strip(),
        )].append(transaction)

    signals = []
    merchant_history = defaultdict(list)
    foreign_history = set()
    ordered_groups = sorted(
        groups.values(),
        key=lambda rows: (
            rows[0].get("date") or rows[0].get("month") or "",
            rows[0].get("card", ""),
            rows[0]["description"].upper().strip(),
        ),
    )
    for rows in ordered_groups:
        debits = [row for row in rows if row.get("type") == "debit"]
        if not debits:
            continue
        refunds = [row for row in rows if row.get("type") == "refund"]
        debit_total = sum(float(row["amount"]) for row in debits)
        refund_total = sum(float(row["amount"]) for row in refunds)
        net = round(max(0.0, debit_total - refund_total), 2)
        if net <= 0:
            continue

        reasons = []
        checks = []
        severity = "low"
        duplicate_amounts = defaultdict(list)
        for row in debits:
            duplicate_amounts[round(float(row["amount"]), 2)].append(row)
        for amount, matching in duplicate_amounts.items():
            if len(matching) < 2:
                continue
            unresolved_value = max(0.0, amount * len(matching) - refund_total)
            unresolved_count = int((unresolved_value + amount - 0.01) // amount)
            unresolved_total = round(unresolved_count * amount, 2)
            if unresolved_count > 1 and unresolved_total >= 40:
                reasons.append(
                    "%d identical S$%.2f charges on the same day with no full reversal"
                    % (unresolved_count, amount)
                )
                checks.append("same-day-duplicate")
                severity = _stronger(
                    severity,
                    "high" if unresolved_total >= 500
                    else "medium" if unresolved_total >= 100
                    else "low",
                )

        key = merchant_key(debits[0]["description"])
        history = merchant_history[key]
        category = debits[0].get("category")

        if not history and net >= 1500:
            reasons.append(
                "First observed day for this merchant in the available history totals S$%.2f"
                % net
            )
            checks.append("first-observed-high-value")
            severity = _stronger(
                severity,
                "high" if net >= 3000 else "medium",
            )

        typical = median(history) if len(history) >= 10 else None
        ratio = net / max(typical or 0, 0.01)
        if typical and net >= 300 and ratio >= 10:
            reasons.append(
                "S$%.2f net charge is %.0fx this merchant's usual S$%.2f"
                % (net, ratio, typical)
            )
            checks.append("merchant-amount-outlier")
            severity = _stronger(
                severity,
                "high" if net >= 1000 or ratio >= 50 else "medium",
            )

        if len(debits) >= 5 and net >= 300:
            reasons.append(
                "%d charges from this merchant on one day total S$%.2f"
                % (len(debits), net)
            )
            checks.append("same-day-burst")
            severity = _stronger(
                severity,
                "high" if len(debits) >= 10 or net >= 1000 else "medium",
            )

        if category == "Subscriptions" and len(debits) == 1 and len(history) >= 4:
            recent = history[-6:]
            subscription_typical = median(recent)
            spread = (
                (max(recent) - min(recent)) / max(subscription_typical, 0.01)
            )
            increase = net - subscription_typical
            if (
                spread <= 0.15
                and increase >= 3
                and net >= subscription_typical * 1.25
            ):
                reasons.append(
                    "Subscription rose from a stable S$%.2f to S$%.2f"
                    % (subscription_typical, net)
                )
                checks.append("subscription-price-jump")
                severity = _stronger(
                    severity,
                    "medium" if net >= 100 else "low",
                )

        foreign_debits = [row for row in debits if row.get("foreign")]
        if (
            foreign_debits
            and key not in foreign_history
            and net >= 200
            and category not in {"Games", "Travel"}
        ):
            currencies = sorted({
                row["foreign"].split(" ", 1)[0] for row in foreign_debits
                if row.get("foreign")
            })
            reasons.append(
                "First foreign-currency charge from this merchant is %s (S$%.2f)"
                % ("/".join(currencies) or "foreign currency", net)
            )
            checks.append("first-foreign-currency-use")
            severity = _stronger(
                severity,
                "medium" if net >= 500 else "low",
            )

        history.extend(float(row["amount"]) for row in debits)
        if foreign_debits:
            foreign_history.add(key)

        if not reasons:
            continue

        affected_ids = sorted(row["id"] for row in debits)
        recognized = all(tx_id in recognized_ids for tx_id in affected_ids)
        representative = sorted(
            debits,
            key=lambda row: (-float(row["amount"]), row["id"]),
        )[0]
        risk = {
            "severity": severity,
            "reasons": reasons,
            "checks": checks,
            "groupIds": affected_ids,
            "reviewAmount": net,
            "recognized": recognized,
        }
        representative["risk"] = risk
        signals.append({
            "transaction": representative,
            "risk": risk,
        })

    unresolved = [signal for signal in signals if not signal["risk"]["recognized"]]
    severity_counts = {
        name: sum(1 for signal in unresolved if signal["risk"]["severity"] == name)
        for name in ("high", "medium", "low")
    }
    return {
        "signals": signals,
        "count": len(unresolved),
        "amount": round(sum(signal["risk"]["reviewAmount"] for signal in unresolved), 2),
        "high": severity_counts["high"],
        "medium": severity_counts["medium"],
        "low": severity_counts["low"],
        "recognized": len(signals) - len(unresolved),
    }
