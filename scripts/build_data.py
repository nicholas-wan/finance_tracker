# Builds the app's main data file. Reads nothing from Finances.xlsx.
#
#   app/data/card_transactions.json   <- parse_cc.py
#   manual/owner_tags.json            exact per-transaction owner
#   manual/owner_rules.json           merchant -> owner fallback
#   manual/legacy_transactions.json   spreadsheet rows for months with no statement
#   manual/salary.json                salary steps, annual income and tax
#   manual/game_sales.json            game account sales
#   manual/identity.json              own-account labels and trusted counterparties
#
# Run parse_cc.py and parse_one.py first, then this.

import calendar
import json
import os
import re
import tempfile
import time
from datetime import datetime

from data_ids import assign_provenance
from risk_checks import detect_risks

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUAL_DIR = os.path.join(REPO_ROOT, "manual")
DATA_DIR = os.path.join(REPO_ROOT, "app", "data")
CARDS_PATH = os.path.join(DATA_DIR, "card_transactions.json")
ACCOUNT_PATH = os.path.join(DATA_DIR, "account_transactions.json")
OUT_PATH = os.path.join(DATA_DIR, "transactions.json")


def next_month_key(month):
    year, number = [int(part) for part in month.split("-")]
    if number == 12:
        return "%04d-01" % (year + 1)
    return "%04d-%02d" % (year, number + 1)


def missing_months(months):
    if not months:
        return []
    present = set(months)
    current = months[0]
    missing = []
    while current < months[-1]:
        current = next_month_key(current)
        if current not in present:
            missing.append(current)
    return missing


def next_cycle_date(last_date, next_month):
    if not last_date:
        return None
    last = datetime.strptime(last_date, "%Y-%m-%d")
    year, month = [int(part) for part in next_month.split("-")]
    day = min(last.day, calendar.monthrange(year, month)[1])
    return "%04d-%02d-%02d" % (year, month, day)

# First matching rule wins. Patterns are case-insensitive substrings.
CATEGORY_RULES = [
    ("Payment", ["PAYMT THRU E-BANK"]),
    ("Rebates", ["CASH REBATE", "ADDITIONAL REBATE", "DEDUCTED UNI$"]),
    ("Work", ["PAYPROGLOBA"]),
    # High-confidence identities checked against public merchant/registry pages.
    ("Fees & charges", ["CARD MEMBERSHIP FEE", "LATE CHARGES", "CR LATE CHARGE", "CR INTEREST", "INTERESTS"]),
    ("Healthcare", [
        "TANGLIN DENTAL",
        "DR KENNETH LEW",
        "PEARL'S OPTICS",
        "DARIOHEALTH",
        "THE DENTAL STUDIO",
        "PARKWAYHEALTH",
        "ANG MO KIO POLYCLINIC",
        "Q&M DENTAL",
        "SP THERAPIN.SG",
    ]),
    ("Home & furnishings", ["AZORA CURTAIN", "BSH HOME APPLIANCES"]),
    ("Entertainment", ["SISTIC", "DERTICKETSERVICE", "HAVE FUN - TPY"]),
    ("Pet care", ["ANIMAL WORLD VET", "PAWSWING"]),
    ("Subscriptions", ["NETFLIX", "SPOTIFY", "YOUTUBE", "DISNEY", "ICLOUD", "GOOGLE ONE",
                       "SUBSCRIPTIONGRAB", "OPENAI", "ANTHROPIC", "PRIME VIDEO", "APPLE.COM/BILL",
                       "AMZNPRIMESG MEMBERSHI", "NAME-CHEAP.COM", "NORDPRODUCTS",
                       "PAYFORGE SERVICES"]),
    ("Transport", ["BUS/MRT", "BUS / MRT", "GRAB*", "WWW.TADA", "TADA.G", "GOJEK", "COMFORT",
                   "CDG TAXI", "SMRT", "UBER *TRIP", "MO.PLA", "GRAB RIDES", "GRAB-EC",
                   "CAUSEWAYLINK", "SP BUS AUNTY", "TADA "]),
    ("Groceries", ["NTUC FAIRPRICE", "FAIRPRICE", "COLD STORAGE", "SHENG SIONG", "GIANT ",
                   "GIANT-", "NTUC FP-", "CHEERS HOLDINGS", "DON DON DONKI",
                   "PRIME SUPERMARKET", "BBQ WHOLESALE CENTRE", "CS FRESH", "JAYA GROCER",
                   "KAPITAN GROCERY", "7-ELEVEN", "7 ELEVEN", "ESSO-CHEERS", "LEE MART",
                   "NTUC FP ", "ACE DYNAMIC HOLDINGS"]),
    ("Food & dining", ["FOOD PANDA", "FP*FOOD", "FOODPANDA", "KOPITIAM", "WOK N RICE", "URBAN GRILL",
                       "WATAMI", "SWENSEN", "FUN TOAST", "POULET", "SUSHI", "LLAO LLAO", "LUCKIN",
                       "DSTA DRINKS", "BOOST JUICE", "MCDONALD", "KFC", "STARBUCKS", "DELIVEROO",
                       "SHOPBACK", "RESTAURANT", "CAFE", "BAKERY", "TOAST", "COFFEE", "FENG SHENG",
                       "LIHO TEA", "YHS", "WARBURG VENDING", "OLD CHANG KEE", "KIMLY", "ENCIK TAN",
                       "MOS BURGER", "BURGER", "SHOKUDO", "F AND B", "HAWKER", "FOOD",
                       "OLD HABITS SG", "KAT CAT", "SYNTHESIS SINGAPORE", "WAA COW",
                       "YAKINIKU", "SUKI-YA", "KISEKI", "HAI DI LAO", "JELEBU DRY LAKSA",
                       "DOMINOS PIZZA", "MARUYA", "SAIZERIYA", "MONSTER CURRY", "BOEUF",
                       "BULGOGI", "K TOWN", "XW PLUS WESTERN", "DA XI-", "JU SHIN JUNG",
                       "PIZZAKAYA", "WOKHEY", "WOK HEY", "AJUMMAS", "GOURMET PARADISE",
                       "HEAVENLYWANG", "KOUFU", "SUBWAY", "MALAYSIA BOLEH", "THE ALLEY",
                       "SHIN-SAPPORO RAMEN", "SUKIYA", "2 THUMBS UP HAINANESE", "4FINGERS",
                       "RASAPURA MASTERS", "TORI-Q", "SMOOY", "TIM HORTONS", "IJOOZ",
                       "XIN WANG", "AMAZING ROASTED DUCK", "LA TABLE D' EMMA", "ABURI-EN",
                       "ISTEAKS", "HOT TOMATO", "CAT AND THE FIDDLE", "THE SOUP SPOON",
                       "SHAKE SHACK", "ANJANA KITCHEN", "CHICHA SAN CHEN", "K COOK KOREAN",
                       "CHIC A BOO", "GRAB SINGAPORE", "NO HORSE RUN", "BJB-NORTHPOINT",
                       "UBER *EATS", "COLLIN'S - SHAW PLAZA", "NENE CHICKEN", "ASTONS-",
                       "JINJJA CHICKEN", "ICS - LUCE", "CRAFT TEA FOX", "MI CUN",
                       "ORANGE & TEAL", "ANDES @ TPY", "MINCHENG BIBIMBAP", "FAMOUS AMOS",
                       "YOSHINOYA", "BK - TERMINAL 4", "PEPPER LUNCH", "FIVE FOOT LANE",
                       "SUNNY KOREAN", "USAGI PAN", "WESTERN BOY", "TAKAGI RAMEN",
                       "KENTUCKY FRIED CHICKEN", "ORIGIN+BLOOM", "BANGKOK BITES", "BJB-",
                       "AUNTIE ANNE'S", "CB&TL", "USS FB-", "STUFF'D", "JIREH MANNA",
                       "MUNCHI", "ATLASVENDING", "BBQ EXPRESS", "WADORI", "FRUIT BOX",
                       "CHATERAISE", "MR BEAN INT", "COCA-COLA SINGAPORE", "KEMING CULTURE",
                       "FU CHAN ST3", "PEPPER GRILL", "KOPIFELLAS",
                       "SEOUL GARDEN - BUGIS", "WUNDERFOLKS",
                       "AWFULLY CHOCOLATE", "XW WESTERN GRILL", "JIA XIANG SARAWAK",
                       "TROPICO KOPI", "HOONG YING ENTERPRISE", "KEIJO SDN BHD",
                       "TINO JC", "Q'SON GROUP", "AURESYS PL", "ABBA OL2",
                       # Inception SG bills the Starbucks drink kiosk, not a game store.
                       "INCEPTION SG",
                       "SB125-AEON BUKIT INDAH", "AIF 111-ORH006", "ANDO.SG"]),
    # "STEAM" and "RIOT" must stay anchored to the biller strings: bare
    # substrings filed steamboat restaurants and MARRIOTT hotels under Games.
    ("Games", ["HOYOVERSE", "COGNOSPHERE", "G2G.COM", "ZEUSX", "STEAMGAMES", "PLAYSTATION", "NINTENDO",
               "RIOT GAMES", "RIOTGAMES", "RIOT*", "KURO GAMES", "GARENA", "CODASHOP", "UNIPIN", "SEA GAMER",
               "ROBLOX", "EPIC GAMES", "XBOX", "BLIZZARD", "G2A", "MIHOYO", "XSOLLA",
               "XD ENTERTAINMENT", "2C2P*MYCARD", "PAYPAL *SPUUKYAZ",
               # MEP* is the payment processor; bahjasuq is the game-top-up storefront.
               "BAHJASUQ",
               "PAYPAL *FIKRIFERDINAN61"]),
    ("Shopping", ["SHOPEE", "LAZADA", "AMAZON", "QOO10", "TAOBAO", "NTE* ORDER", "ORDER 20",
                  "UNIQLO", "IKEA", "JANNPAUL", "COURTS SINGAPORE", "CAPITALAND VOUCHER",
                  "THE WALLET SHOP", "G2000", "TRIUMPH INT", "BURGA", "TAKASHIMAYA",
                  "DAISO JAPAN", "MOBILE FASHION", "SIMPLY TOYS", "VINTAGE SAPPHIRE",
                  "MR DIY", "TELECOM EQUIPMENT PL-WIRE", "MOMENTS SINGAPORE"]),
    ("Sports & fitness", ["MYACTIVESG", "ACTIVESG", "DECATHLON", "GYM", "FITNESS",
                          "SPORTS DIRECT", "HELLO SPORTS", "GALA SPORTS"]),
    ("Personal care", ["KCUTS", "SALON", "BARBER", "GUARDIAN", "WATSONS", "WATSON'S",
                       "VENUS BEAUTY", "HOCKHUA TONIC", "ZTP GINSENG"]),
    ("Insurance", ["PRUDENTIAL", "TOKIO MARINE", "FWD ", "FWD SINGAPORE", "AIA ", "GREAT EASTERN",
                   "NTUC INCOME", "SINGLIFE", "HL ASSURANCE"]),
    ("Bills & utilities", ["SINGTEL", "STARHUB", "M1 ", "AXS ", "AXS PTE", "SP SERVICES", "SP DIGITAL",
                           "SIMBA", "CIRCLES", "GIGA", "OPEN.GOV.SG"]),
    ("Travel", ["AIRLINE", "SINGAPOREAIR", "SCOOT", "JETSTAR", "AGODA", "BOOKING.COM", "AIRBNB",
                "KLOOK", "KKDAY", "ROTTNEST EXPRESS", "TRIP.COM", "HOTEL",
                "USCUSTOMS ESTA", "IVISA SERVICES", "IMMIGRATION CANADA", "AUSTRALIANETA"]),
]

# Within Games, which storefront or publisher the charge belongs to. Statements
# name the biller, not the title, so this is the finest grain available.
GAME_RULES = [
    ("HoYoverse", ["HOYOVERSE", "COGNOSPHERE", "MIHOYO"]),
    ("Kuro Games", ["KURO GAMES"]),
    ("Steam", ["STEAMGAMES"]),
    ("G2G marketplace", ["G2G.COM"]),
    ("ZeusX marketplace", ["ZEUSX"]),
    ("PlayStation", ["PLAYSTATION", "PSN"]),
    ("Nintendo", ["NINTENDO"]),
    ("Xbox", ["XBOX"]),
    ("Epic Games", ["EPIC GAMES"]),
    ("Riot Games", ["RIOT GAMES", "RIOTGAMES", "RIOT*"]),
    ("Garena", ["GARENA"]),
    ("Roblox", ["ROBLOX"]),
    # Billed as "MEP*bahjasuq"/"bahjasuq"; MEP* is the processor, bahjasuq the storefront.
    ("Chaos Zero Nightmare", ["BAHJASUQ"]),
    ("Top-up sites", ["CODASHOP", "UNIPIN", "SEA GAMER", "G2A"]),
]


def load(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def manual(name, default=None):
    return load(os.path.join(MANUAL_DIR, name), default)


def categorize(description):
    d = description.upper()
    for category, patterns in CATEGORY_RULES:
        for p in patterns:
            if p in d:
                return category
    return "Other"


def game_of(description):
    d = description.upper()
    for name, patterns in GAME_RULES:
        for p in patterns:
            if p in d:
                return name
    return "Other games"


# Sales name a title; spending names the biller. This maps a sold title onto the
# spending bucket so the Games tab can filter both together.
SALE_PUBLISHERS = [
    ("HoYoverse", ["HONKAI", "GENSHIN", "ZENLESS"]),
    ("Kuro Games", ["WUTHERING", "PUNISHING"]),
]


def publisher_of(game):
    g = game.upper()
    for name, patterns in SALE_PUBLISHERS:
        for p in patterns:
            if p in g:
                return name
    return game


def tag_key(month, description, amount, credit):
    return "|".join([month, description.upper()[:60], "%.2f" % amount, "C" if credit else "D"])


def merchant_key(description):
    s = description.upper()
    s = re.sub(r"GPC-[0-9A-Z]+", "", s)
    s = re.sub(r"[0-9]{4,}", "", s)
    s = re.sub(r"[^A-Z ]+", " ", s)
    return " ".join(s.split())[:26]


def main():
    cards = load(CARDS_PATH)
    if not cards:
        raise SystemExit("No card_transactions.json - run scripts/parse_cc.py first")

    rows = list(cards.get("transactions", []))
    covered = set(cards.get("months", []))

    legacy = list(manual("legacy_transactions.json", {}).get("transactions", []))
    for source_line, row in enumerate(legacy, 1):
        row["_sourceLine"] = source_line
    assign_provenance(
        legacy, "legacy-manual", "legacy_transactions.json", verified=False
    )
    filled = sorted({t["month"] for t in legacy if t["month"] not in covered})
    rows.extend(t for t in legacy if t["month"] not in covered)

    owner_tag_data = manual("owner_tags.json", {})
    owner_tags = owner_tag_data.get("tags", {})
    owner_tags_by_id = owner_tag_data.get("tagsById", {})
    remarks_by_id = manual("transaction_remarks.json", {}).get("remarksById", {})
    transaction_overrides = manual(
        "transaction_overrides.json", {}
    ).get("overridesById", {})
    rules_file = manual("owner_rules.json", {})
    owner_rules = rules_file.get("rules", {})
    # Merchants whose rule you have signed off on. Their rows stop appearing in
    # the review list, so only genuinely unchecked attributions surface.
    confirmed_patterns = [c.upper() for c in rules_file.get("confirmed", [])]

    def rule_is_confirmed(description):
        m = merchant_key(description)
        return any(p in m for p in confirmed_patterns)

    # Repeat charges share a tag key, so hand out that key's owners one per row.
    # Rows are processed in a stable order, so the same row gets the same owner
    # on every build.
    rows.sort(key=lambda r: (r["month"], r.get("date") or "", r["description"], r["amount"]))
    tag_cursor = {}

    transactions = []
    tagged_id = tagged_exact = tagged_rule = 0
    for r in rows:
        rule_category = categorize(r["description"])
        override = transaction_overrides.get(r["id"], {})
        category = override.get("category", rule_category)
        if category == "Payment":
            tx_type = "payment"
        elif r.get("credit"):
            tx_type = "refund"
        else:
            tx_type = "debit"

        # Always consume the matching legacy slot, even when a stable-ID tag now
        # wins. This preserves the old duplicate-row alignment during migration.
        key = tag_key(r["month"], r["description"], r["amount"], bool(r.get("credit")))
        available = owner_tags.get(key)
        if isinstance(available, str):
            available = [available]
        legacy_owner = None
        if available:
            i = tag_cursor.get(key, 0)
            if i < len(available):
                legacy_owner = available[i]
                tag_cursor[key] = i + 1

        owner = owner_tags_by_id.get(r["id"])
        owner_source = None
        if owner:
            tagged_id += 1
            owner_source = "exact-id"
        else:
            owner = legacy_owner
            if owner:
                tagged_exact += 1
                owner_source = "exact-legacy"
            else:
                owner = owner_rules.get(merchant_key(r["description"]))
                if owner:
                    tagged_rule += 1
                    owner_source = ("merchant-rule-confirmed"
                                    if rule_is_confirmed(r["description"]) else "merchant-rule")
        if not owner:
            owner_source = "unassigned"
        record = {
            "id": r["id"],
            "date": r.get("date"),
            "postedDate": r.get("postedDate"),
            "month": r["month"],
            "card": r.get("card", "UOB ONE CARD"),
            "description": r["description"],
            "amount": r["amount"],
            "type": tx_type,
            "owner": owner or "Untagged",
            "ownerSource": owner_source,
            "category": category,
            "ruleCategory": rule_category,
            "provenance": r["provenance"],
        }
        if r.get("foreign"):
            record["foreign"] = r["foreign"]
        display_name = override.get("displayName")
        if display_name:
            record["displayName"] = display_name
        if category == "Games":
            record["game"] = game_of(r["description"])
        remark = remarks_by_id.get(r["id"])
        if remark:
            record["remark"] = remark
        transactions.append(record)

    transactions.sort(
        key=lambda t: (t["month"], t["date"] or "", t["description"], t["id"])
    )

    # Recognition is stored per signal (rows + checks that fired), so a new
    # reason on an already-acknowledged transaction surfaces again.
    risk_reviews = manual("risk_reviews.json", {"recognizedSignals": []})
    risk_summary = detect_risks(
        transactions,
        merchant_key,
        risk_reviews.get("recognizedSignals", []),
    )

    months = sorted({t["month"] for t in transactions})
    salary = manual("salary.json", {})
    sales = manual("game_sales.json", {}).get("sales", [])
    settlements = manual("settlements.json", {"openingBalances": []})
    # Only the two fields the dashboard actually reads are published. The
    # holder's name and the fixed-deposit account numbers stay in manual/,
    # because nothing in app/ needs them and app/data/ is easy to copy around.
    identity_file = manual("identity.json", {})
    identity = {
        "knownAccounts": identity_file.get("knownAccounts") or {},
        "trustedCounterparties": identity_file.get("trustedCounterparties") or [],
    }
    for s in sales:
        s["publisher"] = publisher_of(s.get("game", ""))
    sales.sort(key=lambda s: (s.get("month", ""), s.get("game", "")))

    review_rows = [
        t for t in transactions
        if t["category"] not in ("Payment", "Rebates")
    ]
    untagged_rows = [t for t in review_rows if t["owner"] == "Untagged"]
    other_rows = [t for t in review_rows if t["category"] == "Other"]
    unverified_rows = [
        t for t in review_rows if not t["provenance"].get("verified")
    ]
    settled = ("exact-id", "exact-legacy", "merchant-rule-confirmed")
    lady_review_rows = [
        t for t in review_rows
        if "LADY" in t["card"].upper() and t["ownerSource"] not in settled
    ]
    # A dated opening balance supersedes earlier settlement activity. Only
    # surface unconfirmed rule assignments that can still change the current
    # position; retain the audit metadata on older rows without warning about it.
    settlement_starts = [
        item.get("from")
        for item in settlements.get("openingBalances", [])
        if isinstance(item, dict) and item.get("from")
    ]
    settlement_start = max(settlement_starts) if settlement_starts else None
    split_review_rows = [
        t for t in review_rows
        if t["ownerSource"] == "merchant-rule"
        and t["owner"] in ("Shared", "Yx")
        and (not settlement_start or t["month"] >= settlement_start)
    ]
    source_counts = {}
    for t in transactions:
        source_type = t["provenance"]["sourceType"]
        source_counts[source_type] = source_counts.get(source_type, 0) + 1
    account_data = load(ACCOUNT_PATH, {})
    ids = [t["id"] for t in transactions]
    missing_provenance = sum(1 for t in transactions if not t.get("provenance"))
    duplicate_ids = len(ids) - len(set(ids))
    pdf_months = sorted(
        cards.get("quality", {}).get("pdfMonths")
        or cards.get("months", [])
    )
    latest_statement_month = pdf_months[-1] if pdf_months else None
    latest_statement_rows = [
        t for t in transactions if t.get("month") == latest_statement_month
    ]
    source_through = max(
        (
            t.get("postedDate") or t.get("date")
            for t in latest_statement_rows
            if t.get("postedDate") or t.get("date")
        ),
        default=None,
    )
    expected_next_month = (
        next_month_key(latest_statement_month)
        if latest_statement_month else None
    )
    freshness = {
        "latestStatementMonth": latest_statement_month,
        "latestStatementFile": (
            cards.get("sourceFiles", {}).get(latest_statement_month)
            if latest_statement_month else None
        ),
        "sourceThrough": source_through,
        "expectedNextStatementMonth": expected_next_month,
        "expectedNextStatementDate": (
            next_cycle_date(source_through, expected_next_month)
            if expected_next_month else None
        ),
        "missingStatementMonths": missing_months(pdf_months),
        "statementCount": len(pdf_months),
    }
    quality = {
        "status": "review" if (untagged_rows or other_rows or unverified_rows
                               or lady_review_rows or split_review_rows
                               or risk_summary["count"]) else "ready",
        "integrity": {
            "uniqueIds": duplicate_ids == 0,
            "duplicateIds": duplicate_ids,
            "missingProvenance": missing_provenance,
            "sourceTransactions": len(rows),
            "outputTransactions": len(transactions),
        },
        "provenance": {
            "sourceCounts": source_counts,
            "unverifiedTransactions": len(unverified_rows),
            "unverifiedMonths": sorted({
                t["month"] for t in unverified_rows
            }),
            "card": cards.get("quality", {}),
            "account": account_data.get("quality", {}),
        },
        "review": {
            "untagged": {
                "count": len(untagged_rows),
                "amount": round(sum(abs(t["amount"]) for t in untagged_rows), 2),
            },
            "otherCategory": {
                "count": len(other_rows),
                "amount": round(sum(abs(t["amount"]) for t in other_rows), 2),
            },
            "ladyRuleOrUnassigned": {
                "count": len(lady_review_rows),
                "amount": round(sum(abs(t["amount"]) for t in lady_review_rows), 2),
            },
            "splitByRule": {
                "count": len(split_review_rows),
                "amount": round(sum(abs(t["amount"]) for t in split_review_rows), 2),
                "owedImpact": round(sum(
                    (t["amount"] if t["type"] == "debit" else -t["amount"])
                    * (0.5 if t["owner"] == "Shared" else 1.0)
                    for t in split_review_rows), 2),
                "merchants": sorted({merchant_key(t["description"]) for t in split_review_rows})[:12],
            },
            "suspicious": {
                "count": risk_summary["count"],
                "amount": risk_summary["amount"],
                "high": risk_summary["high"],
                "medium": risk_summary["medium"],
                "low": risk_summary["low"],
                "recognized": risk_summary["recognized"],
            },
        },
        "ownerTags": {
            "stableId": tagged_id,
            "legacyExact": tagged_exact,
            "merchantRule": tagged_rule,
            "unassigned": len(untagged_rows),
        },
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    # Write through a per-process temp file so an interrupted build can never
    # leave a truncated transactions.json, and so a manual run racing the
    # server's rebuild subprocess cannot interleave writes into one shared
    # OUT_PATH + ".tmp". os.replace is retried because on Windows it fails
    # while a reader (a second browser tab mid-refresh) still holds the
    # destination open; serve.py has the same guard.
    descriptor, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(OUT_PATH) + ".", suffix=".tmp", dir=DATA_DIR)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            json.dump({
                "currency": "SGD",
                "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "months": months,
                "salarySteps": salary.get("steps", []),
                "salaryYears": salary.get("years", []),
                "gameSales": sales,
                "settlements": settlements,
                "identity": identity,
                "freshness": freshness,
                "quality": quality,
                "transactions": transactions,
            }, f, indent=1)
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

    spend = sum(t["amount"] for t in transactions if t["type"] == "debit")
    refunds = sum(t["amount"] for t in transactions if t["type"] == "refund")
    other = sum(1 for t in transactions if t["category"] == "Other")
    untagged = sum(1 for t in transactions if t["owner"] == "Untagged")
    print("Wrote %d transactions across %d months -> %s"
          % (len(transactions), len(months), os.path.relpath(OUT_PATH, REPO_ROOT)))
    print("Debits S$%s, refunds S$%s, uncategorized %d (%.0f%%)"
          % (format(spend, ",.2f"), format(refunds, ",.2f"), other,
             100.0 * other / max(len(transactions), 1)))
    print("Owner: %d by stable ID, %d from legacy exact tags, %d from merchant rules, %d untagged"
          % (tagged_id, tagged_exact, tagged_rule, untagged))
    print("Salary steps %d, annual rows %d, game sales %d"
          % (len(salary.get("steps", [])), len(salary.get("years", [])), len(sales)))
    print("Remarks %d" % len(remarks_by_id))
    print("Transaction overrides %d" % len(transaction_overrides))
    print("Transaction checks: %d to review, %d recognized"
          % (risk_summary["count"], risk_summary["recognized"]))
    if filled:
        print("No statement for %s - used spreadsheet rows from legacy_transactions.json"
              % ", ".join(filled))


if __name__ == "__main__":
    main()
