"""Conservative, explainable card-transaction anomaly checks.

These checks surface transactions for human review; they do not label fraud.
Refunds are netted before duplicate and amount checks so reversals do not create
false alarms. Every signal uses fields printed on the statement or stable
history derived from earlier statements.

Recognition is per *signal*, not per transaction. A signal's identity is a
hash over the rows it covers plus the checks that fired, so acknowledging a
duplicate pair does not silently suppress a different check that the same rows
trip later.
"""

import hashlib
from collections import defaultdict
from datetime import date
from statistics import median


# Card-bill payments and rebates are statement mechanics, not purchases, and
# every row would otherwise trip the history-based checks.
EXCLUDED_CATEGORIES = {"Payment", "Rebates"}
# Premiums and bank fees are large by nature, so the amount-based checks would
# cry wolf - but a double-billed premium is exactly a duplicate, so these
# categories run the duplicate/burst checks and skip the amount checks. They
# used to be excluded outright, which meant a duplicated insurance premium
# could never flag.
DUPLICATE_ONLY_CATEGORIES = {"Fees & charges", "Insurance"}
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}

# Card reversals rarely post on the charge date. A week covers the usual
# settlement lag without letting an unrelated later credit erase a genuine
# anomaly. Refunds are matched on merchant key, not on the raw descriptor,
# because the descriptor carries per-authorisation noise.
REFUND_WINDOW_DAYS = 7

# Cross-day netting only moves material credits. Without this, a S$1.09 scrap
# left over from one day's reversal drifts onto a neighbouring day and quietly
# shaves the review amount there for no reason a reader could follow.
REFUND_CROSS_DAY_MIN = 40.0

# The median-based outlier check needs a real distribution behind it, so it
# only runs from this many prior rows onwards.
OUTLIER_MIN_HISTORY = 10

# Between 1 and OUTLIER_MIN_HISTORY - 1 prior rows there is no trustworthy
# median, but "nothing at all" is the wrong answer: nine S$12 charges followed
# by S$5,000 is exactly the shape worth surfacing. Compare against the prior
# maximum instead and require the charge to be both a large multiple of
# anything seen before and large in absolute terms, so ordinary ramp-ups
# (S$12 -> S$60) stay quiet.
SHORT_HISTORY_MULTIPLE = 4.0
SHORT_HISTORY_FLOOR = 500.0

# A currency only counts as "known" for a merchant once a charge in it was big
# enough to have been surfaced. A S$5 test charge must not immunise the
# merchant against a S$3,000 charge later, in that currency or another one.
FOREIGN_MIN_REVIEW = 200.0

# Charges below this are noise for the duplicate check regardless of count.
DUPLICATE_MIN_TOTAL = 40.0

# A material credit from a merchant that was never charged before is the shape
# of a fake-refund setup, so it deserves a look. Ordinary slow refunds stay
# quiet because their merchant has prior charges.
UNMATCHED_CREDIT_MIN = 200.0

# Card testing: a stolen card is probed with a few tiny charges at merchants
# the card has never seen, all on one day, before a real charge is tried.
# Three or more such charges on one day is the shape worth a look; the amount
# ceiling keeps ordinary first visits to a new hawker stall out of it.
CARD_TEST_MAX_AMOUNT = 20.0
CARD_TEST_MIN_MERCHANTS = 3

# New-merchant velocity: a merchant first seen only days ago that is already
# charging on its third distinct day is either a new habit or a compromised
# card being drained in small pieces. The window is short and the total floor
# keeps a new coffee place from tripping it.
VELOCITY_WINDOW_DAYS = 3
VELOCITY_MIN_DAYS = 3
VELOCITY_MIN_TOTAL = 100.0

# Both "never seen before" checks are meaningless at the start of the
# available history, when every merchant is new. They stay silent until this
# many days of statements exist before the day under review.
HISTORY_WARMUP_DAYS = 30

CENT = 0.005


def _stronger(current, candidate):
    if SEVERITY_RANK[candidate] > SEVERITY_RANK[current]:
        return candidate
    return current


def _rule_category(row):
    """Category as the rules derive it, ignoring the user's override.

    Category overrides are a labelling choice. Letting them drive the risk
    filter means re-tagging a flagged charge as Insurance deletes its signal
    with no trace, so every exclusion and exemption below reads the rule
    category and falls back to the plain category only when the build did not
    supply one.
    """
    return row.get("ruleCategory") or row.get("category")


def _day_ordinal(value):
    """Day number for "YYYY-MM-DD"; month-only rows anchor to the 1st."""
    if not value:
        return None
    parts = str(value).split("-")
    try:
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day).toordinal()
    except (IndexError, ValueError):
        return None


def signal_key(group_ids, checks):
    """Stable identity for one signal: which rows, and which checks fired.

    Both parts matter. Keying on rows alone is what let a recognized duplicate
    pair suppress a later first-observed-high-value check on the same rows.
    """
    payload = "|".join(sorted(group_ids)) + "||" + "|".join(sorted(set(checks)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def recognized_keys(recognized_signals):
    """Accept stored entry dicts, bare key strings, or a mix."""
    keys = set()
    for entry in recognized_signals or []:
        if isinstance(entry, str):
            keys.add(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("key"), str):
            keys.add(entry["key"])
    return keys


def _collect(transactions, merchant_key):
    """Split rows into per-day debit groups and a per-merchant refund pool."""
    debit_groups = defaultdict(list)
    refunds = defaultdict(list)
    for transaction in transactions:
        if _rule_category(transaction) in EXCLUDED_CATEGORIES:
            continue
        when = transaction.get("date") or transaction.get("month")
        key = merchant_key(transaction["description"])
        row_type = transaction.get("type")
        if row_type == "refund":
            refunds[key].append({
                "when": when,
                "day": _day_ordinal(when),
                "amount": round(float(transaction["amount"]), 2),
                "used": 0.0,
                "row": transaction,
            })
        elif row_type == "debit":
            debit_groups[(when, key)].append(transaction)
    for pool in refunds.values():
        pool.sort(key=lambda refund: (refund["when"] or "", refund["amount"]))
    return debit_groups, refunds


def _allocate_refunds(debit_groups, refunds):
    """Decide which day's charges each refund reverses.

    Same-day reversals win outright: a statement showing S$960 charged and
    S$813.58 + S$146.42 credited back that same day is describing one
    transaction, and no nearby day has a better claim on that money. Only what
    is left over travels, and only if it is material and lands inside the
    netting window, nearest day first. Handing credits out earliest-day-first
    instead would let a delayed reversal cancel an unrelated charge that merely
    happened to come sooner.
    """
    capacity = {
        group_id: round(sum(float(row["amount"]) for row in rows), 2)
        for group_id, rows in debit_groups.items()
    }
    allocations = defaultdict(list)

    def give(group_id, refund):
        value = min(refund["amount"] - refund["used"], capacity[group_id])
        if value <= CENT:
            return
        refund["used"] += value
        capacity[group_id] -= value
        allocations[group_id].append({"amount": refund["amount"], "value": value})

    for key, pool in refunds.items():
        for refund in pool:
            group_id = (refund["when"], key)
            if group_id in capacity:
                give(group_id, refund)

    groups_by_merchant = defaultdict(list)
    for when, key in debit_groups:
        groups_by_merchant[key].append((when, key))

    for key, pool in refunds.items():
        for refund in pool:
            if refund["day"] is None:
                continue
            candidates = []
            for group_id in groups_by_merchant.get(key, ()):
                if group_id[0] == refund["when"]:
                    continue
                day = _day_ordinal(group_id[0])
                if day is None:
                    continue
                # A refund reverses an earlier charge, never a later one. The
                # old abs() window let a credit suppress signals on charges up
                # to a week in its future - a temporally impossible reversal.
                distance = refund["day"] - day
                if 0 < distance <= REFUND_WINDOW_DAYS:
                    candidates.append((distance, group_id[0], group_id))
            for _, _, group_id in sorted(candidates):
                if refund["amount"] - refund["used"] < REFUND_CROSS_DAY_MIN:
                    break
                give(group_id, refund)
    return allocations


def _apply_refunds(debits, contributions):
    """Net this group's allocated refunds against its charges.

    Exact amount matches are assigned to their own duplicate bucket first, so a
    single S$300 refund cancels one S$300 charge instead of being subtracted
    from every bucket at once. Whatever is left over is a general pool applied
    to the largest outstanding bucket first.
    """
    debit_total = round(sum(float(row["amount"]) for row in debits), 2)
    buckets = defaultdict(list)
    for row in debits:
        buckets[round(float(row["amount"]), 2)].append(row)
    offsets = {amount: 0.0 for amount in buckets}
    pending = [dict(entry) for entry in contributions]
    allocated = 0.0

    for amount in sorted(buckets, reverse=True):
        outstanding = amount * len(buckets[amount]) - offsets[amount]
        for entry in pending:
            if outstanding <= CENT:
                break
            if entry["value"] <= CENT or abs(entry["amount"] - amount) > CENT:
                continue
            take = min(entry["value"], outstanding)
            entry["value"] -= take
            offsets[amount] += take
            outstanding -= take
            allocated += take

    pool = 0.0
    for entry in pending:
        if entry["value"] > CENT:
            pool += entry["value"]
            allocated += entry["value"]
            entry["value"] = 0.0

    for amount in sorted(buckets, reverse=True):
        if pool <= CENT:
            break
        outstanding = max(0.0, amount * len(buckets[amount]) - offsets[amount])
        take = min(pool, outstanding)
        offsets[amount] += take
        pool -= take

    net = round(max(0.0, debit_total - allocated), 2)
    return net, buckets, offsets


def detect_risks(transactions, merchant_key, recognized_signals=None):
    """Annotate flagged transactions and return unresolved review totals."""
    known = recognized_keys(recognized_signals)

    # A second call on the same rows must not inherit the first call's verdict.
    for transaction in transactions:
        transaction.pop("risk", None)

    debit_groups, refund_pool = _collect(transactions, merchant_key)
    allocations = _allocate_refunds(debit_groups, refund_pool)

    signals = []
    merchant_history = defaultdict(list)
    # Distinct charge days and per-day nets per merchant, for the velocity check.
    merchant_days = defaultdict(list)
    merchant_net = defaultdict(list)
    foreign_history = set()
    history_days = [_day_ordinal(when) for when, _ in debit_groups]
    history_start = min((d for d in history_days if d is not None), default=None)

    def warmed_up(day):
        return (day is not None and history_start is not None
                and day - history_start >= HISTORY_WARMUP_DAYS)

    for when, key in sorted(debit_groups, key=lambda item: (item[0] or "", item[1])):
        debits = debit_groups[(when, key)]
        net, buckets, offsets = _apply_refunds(
            debits, allocations.get((when, key), ())
        )
        if net <= 0:
            continue

        reasons = []
        checks = []
        severity = "low"

        for amount in sorted(buckets, reverse=True):
            matching = buckets[amount]
            if len(matching) < 2:
                continue
            unresolved_value = max(0.0, amount * len(matching) - offsets[amount])
            unresolved_count = int((unresolved_value + amount - 0.01) // amount)
            unresolved_total = round(unresolved_count * amount, 2)
            if unresolved_count > 1 and unresolved_total >= DUPLICATE_MIN_TOTAL:
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

        history = merchant_history[key]
        category = _rule_category(debits[0])
        # Duplicate-only categories still reach this loop for the duplicate and
        # burst checks above/below, but their amounts are routinely large, so
        # the history/amount checks would flag every premium.
        amount_checks = category not in DUPLICATE_ONLY_CATEGORIES

        if amount_checks and not history and net >= 1500:
            reasons.append(
                "First observed day for this merchant in the available history totals S$%.2f"
                % net
            )
            checks.append("first-observed-high-value")
            severity = _stronger(
                severity,
                "high" if net >= 3000 else "medium",
            )

        if amount_checks and 0 < len(history) < OUTLIER_MIN_HISTORY:
            prior_max = max(history)
            threshold = max(prior_max * SHORT_HISTORY_MULTIPLE, SHORT_HISTORY_FLOOR)
            if net >= threshold:
                reasons.append(
                    "S$%.2f net charge is far above the S$%.2f most this merchant"
                    " had charged across %d earlier %s"
                    % (net, prior_max, len(history),
                       "row" if len(history) == 1 else "rows")
                )
                checks.append("short-history-spike")
                severity = _stronger(
                    severity,
                    "high" if net >= 3000 or net >= prior_max * 20 else "medium",
                )

        typical = median(history) if len(history) >= OUTLIER_MIN_HISTORY else None
        ratio = net / max(typical or 0, 0.01)
        if amount_checks and typical and net >= 300 and ratio >= 10:
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

        day = _day_ordinal(when)
        days = merchant_days[key]
        if (
            amount_checks
            and warmed_up(day)
            and days
            and day - days[0] <= VELOCITY_WINDOW_DAYS
            and day not in days
            and len(days) + 1 >= VELOCITY_MIN_DAYS
        ):
            since = sum(merchant_net[key]) + net
            if since >= VELOCITY_MIN_TOTAL:
                reasons.append(
                    "New merchant charged on %d of its first %d days, S$%.2f so far"
                    % (len(days) + 1, day - days[0] + 1, since)
                )
                checks.append("new-merchant-velocity")
                severity = _stronger(
                    severity,
                    "high" if since >= 500 else "medium",
                )
        if day is not None and day not in days:
            days.append(day)
        merchant_net[key].append(net)

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
        currencies = sorted({
            row["foreign"].split(" ", 1)[0] for row in foreign_debits
            if row.get("foreign")
        })
        unseen = [code for code in currencies if (key, code) not in foreign_history]
        surfaced_foreign = False
        if (
            amount_checks
            and unseen
            and net >= FOREIGN_MIN_REVIEW
            and category not in {"Games", "Travel"}
        ):
            reasons.append(
                "First foreign-currency charge from this merchant is %s (S$%.2f)"
                % ("/".join(unseen) or "foreign currency", net)
            )
            checks.append("first-foreign-currency-use")
            severity = _stronger(
                severity,
                "medium" if net >= 500 else "low",
            )
            surfaced_foreign = True

        history.extend(float(row["amount"]) for row in debits)
        if currencies and (surfaced_foreign or net >= FOREIGN_MIN_REVIEW):
            for code in currencies:
                foreign_history.add((key, code))

        if not reasons:
            continue

        group_ids = sorted(row["id"] for row in debits)
        identity = signal_key(group_ids, checks)
        representative = sorted(
            debits,
            key=lambda row: (-float(row["amount"]), row["id"]),
        )[0]
        risk = {
            "key": identity,
            "severity": severity,
            "reasons": reasons,
            "checks": checks,
            "groupIds": group_ids,
            "reviewAmount": net,
            "recognized": identity in known,
        }
        # Every row in the group carries the signal, so opening any member of a
        # duplicate pair shows the check. ``primary`` marks the one row the
        # review queue counts, keeping row counts equal to signal counts.
        for row in debits:
            row["risk"] = dict(risk, primary=row["id"] == representative["id"])
        signals.append({
            "transaction": representative,
            "risk": representative["risk"],
        })

    # Credit-side check: every check above fires on debits, so a fabricated
    # "refund" from a merchant that never charged this card sailed through.
    # Ordinary slow refunds are quiet here because their merchant has at least
    # one charge on or before the credit date.
    first_debit_day = {}
    for (when, key) in debit_groups:
        day = _day_ordinal(when)
        if day is None:
            continue
        if key not in first_debit_day or day < first_debit_day[key]:
            first_debit_day[key] = day

    # Card testing: several tiny charges on one day, each from a merchant the
    # card had never seen before that day. The per-merchant loop above cannot
    # see this shape because each probe is a different merchant.
    probes_by_day = defaultdict(list)
    for (when, key), debits in debit_groups.items():
        day = _day_ordinal(when)
        if not warmed_up(day) or first_debit_day.get(key) != day:
            continue
        if _rule_category(debits[0]) in DUPLICATE_ONLY_CATEGORIES:
            continue
        small = [row for row in debits if float(row["amount"]) <= CARD_TEST_MAX_AMOUNT]
        if small and len(small) == len(debits):
            probes_by_day[when].append((key, small))
    for when in sorted(probes_by_day):
        probes = probes_by_day[when]
        if len(probes) < CARD_TEST_MIN_MERCHANTS:
            continue
        rows = [row for _, small in probes for row in small]
        total = round(sum(float(row["amount"]) for row in rows), 2)
        checks = ["card-testing"]
        group_ids = sorted(row["id"] for row in rows)
        identity = signal_key(group_ids, checks)
        risk = {
            "key": identity,
            "severity": "high" if len(probes) >= 5 else "medium",
            "reasons": [
                "%d small charges (S$%.2f or less each) on one day from %d merchants"
                " never seen before, S$%.2f in total"
                % (len(rows), CARD_TEST_MAX_AMOUNT, len(probes), total)
            ],
            "checks": checks,
            "groupIds": group_ids,
            "reviewAmount": total,
            "recognized": identity in known,
        }
        representative = sorted(rows, key=lambda row: (-float(row["amount"]), row["id"]))[0]
        for row in rows:
            # A probe row may already carry a per-merchant signal; the
            # card-testing signal is the more specific story, so it wins.
            row["risk"] = dict(risk, primary=row["id"] == representative["id"])
        signals = [s for s in signals if s["transaction"]["id"] not in set(group_ids)]
        signals.append({"transaction": representative, "risk": representative["risk"]})

    for key, pool in refund_pool.items():
        for refund in pool:
            if refund["amount"] < UNMATCHED_CREDIT_MIN or refund["day"] is None:
                continue
            earliest = first_debit_day.get(key)
            if earliest is not None and earliest <= refund["day"]:
                continue
            row = refund["row"]
            checks = ["unmatched-large-credit"]
            group_ids = [row["id"]]
            identity = signal_key(group_ids, checks)
            risk = {
                "key": identity,
                "severity": "high" if refund["amount"] >= 1000 else "medium",
                "reasons": [
                    "S$%.2f credited by a merchant with no earlier charge "
                    "in the available history" % refund["amount"]
                ],
                "checks": checks,
                "groupIds": group_ids,
                "reviewAmount": refund["amount"],
                "recognized": identity in known,
            }
            row["risk"] = dict(risk, primary=True)
            signals.append({"transaction": row, "risk": row["risk"]})

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
