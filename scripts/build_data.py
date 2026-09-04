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
import math
import os
import re
import tempfile
import time
from datetime import datetime

from data_ids import assign_provenance
from risk_checks import detect_risks

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUAL_DIR = os.path.join(REPO_ROOT, "manual")
DATA_DIR = os.environ.get(
    "FINANCE_DATA_DIR", os.path.join(REPO_ROOT, "app", "data")
)
CARDS_PATH = os.path.join(DATA_DIR, "card_transactions.json")
ACCOUNT_PATH = os.path.join(DATA_DIR, "account_transactions.json")
OUT_PATH = os.path.join(DATA_DIR, "transactions.json")

FOODPANDA_CATEGORIES = ("Food & dining", "Groceries")
FOODPANDA_FULFILMENT = ("delivery", "pickup")
SHOPEE_STATUSES = ("to-receive", "completed", "rated")
GRAB_CATEGORIES = ("Food & dining", "Groceries", "Transport")
GRAB_PROFILES = ("personal", "business", "corporate", "unknown")
INSURANCE_BENEFITS = (
    "death", "tpd", "earlyCriticalIllness", "criticalIllness",
    "disabilityIncome", "personalAccident", "hospitalSurgicalAnnualLimit",
)


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

# First matching rule wins. categorize() matches these case-insensitively against
# the description with its whitespace collapsed and one leading and one trailing
# space added, so a pattern written with an explicit edge space is anchored to a
# word edge - the same technique parse_one.FLOW_RULES uses.
#
# Short or generic tokens must use it. Bare substrings filed BLOOM1 SHOP under
# bills ("M1 "), MALAIA CAFE under insurance ("AIA "), SEAFOOD and WATAMIFOODS
# rows off a bare "FOOD", and matched any word merely containing SALON, HOTEL,
# TOAST or BURGER. A trailing space as well (" GYM ", " M1 ", " GIGA ") is for
# tokens that are also the prefix of an unrelated word - GYMBOREE, GIGABYTE -
# which a leading space alone would not exclude.
#
# Multi-word or distinctive biller strings ("NTUC FAIRPRICE", "PAYMT THRU
# E-BANK", "GRAB*", "STEAMGAMES") stay plain substrings: anchoring buys nothing
# and would only lose glued forms. Where a real statement glues an anchored
# token to punctuation instead of a space, the separator variant is listed next
# to it ("-COFFEE" for WTR*COLUMBUS-COFFEE-CO, ".CAFE" for TBG-TP L.CAFE), and
# descriptors that legitimately run into other text keep an unanchored variant
# ("FOODPANDA", "FP*FOOD", "WATAMI", "MOS BURGER").
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
                   "CDG TAXI", " SMRT", "UBER *TRIP", "MO.PLA", "GRAB RIDES", "GRAB-EC",
                   "CAUSEWAYLINK", "SP BUS AUNTY", " TADA "]),
    ("Groceries", ["NTUC FAIRPRICE", "FAIRPRICE", "COLD STORAGE", "SHENG SIONG", " GIANT ",
                   "GIANT-", "NTUC FP-", "CHEERS HOLDINGS", "DON DON DONKI",
                   "PRIME SUPERMARKET", "BBQ WHOLESALE CENTRE", "CS FRESH", "JAYA GROCER",
                   "KAPITAN GROCERY", "7-ELEVEN", "7 ELEVEN", "ESSO-CHEERS", "LEE MART",
                   "NTUC FP ", "ACE DYNAMIC HOLDINGS"]),
    ("Food & dining", ["FOOD PANDA", "FP*FOOD", "FOODPANDA", "KOPITIAM", "WOK N RICE", "URBAN GRILL",
                       "WATAMI", "SWENSEN", "FUN TOAST", "POULET", "SUSHI", "LLAO LLAO", "LUCKIN",
                       "DSTA DRINKS", "BOOST JUICE", "MCDONALD", " KFC", "STARBUCKS", "DELIVEROO",
                       "SHOPBACK", "RESTAURANT", " CAFE", "-CAFE", ".CAFE", "BAKERY", " TOAST",
                       " COFFEE", "-COFFEE", "FENG SHENG",
                       "LIHO TEA", " YHS", "WARBURG VENDING", "OLD CHANG KEE", "KIMLY", "ENCIK TAN",
                       "MOS BURGER", " BURGER", "SHOKUDO", " F AND B", "HAWKER", " FOOD", " SEAFOOD",
                       "OLD HABITS SG", "KAT CAT", "SYNTHESIS SINGAPORE", "WAA COW",
                       "YAKINIKU", "SUKI-YA", "KISEKI", "HAI DI LAO", "JELEBU DRY LAKSA",
                       "DOMINOS PIZZA", "MARUYA", "SAIZERIYA", "MONSTER CURRY", "BOEUF",
                       "BULGOGI", " K TOWN", "XW PLUS WESTERN", "DA XI-", "JU SHIN JUNG",
                       "PIZZAKAYA", "WOKHEY", "WOK HEY", "AJUMMAS", "GOURMET PARADISE",
                       "HEAVENLYWANG", "KOUFU", " SUBWAY", "MALAYSIA BOLEH", "THE ALLEY",
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
               "ROBLOX", "EPIC GAMES", "XBOX", "BLIZZARD", " G2A", "MIHOYO", "XSOLLA",
               "XD ENTERTAINMENT", "2C2P*MYCARD", "PAYPAL *SPUUKYAZ",
               # MEP* is the payment processor; bahjasuq is the game-top-up storefront.
               "BAHJASUQ",
               "PAYPAL *FIKRIFERDINAN61"]),
    ("Shopping", ["SHOPEE", "LAZADA", "AMAZON", "QOO10", "TAOBAO", "NTE* ORDER", " ORDER 20",
                  "UNIQLO", " IKEA", "JANNPAUL", "COURTS SINGAPORE", "CAPITALAND VOUCHER",
                  "THE WALLET SHOP", " G2000", "TRIUMPH INT", " BURGA", "TAKASHIMAYA",
                  "DAISO JAPAN", "MOBILE FASHION", "SIMPLY TOYS", "VINTAGE SAPPHIRE",
                  " MR DIY", "TELECOM EQUIPMENT PL-WIRE", "MOMENTS SINGAPORE"]),
    # " GYM " needs both edges: GYMBOREE is a children's brand, not a gym.
    ("Sports & fitness", ["MYACTIVESG", "ACTIVESG", "DECATHLON", " GYM ", "FITNESS",
                          "SPORTS DIRECT", "HELLO SPORTS", "GALA SPORTS"]),
    ("Personal care", [" KCUTS", " SALON", " BARBER", " GUARDIAN", "WATSONS", "WATSON'S",
                       "VENUS BEAUTY", "HOCKHUA TONIC", "ZTP GINSENG"]),
    ("Insurance", ["PRUDENTIAL", "TOKIO MARINE", " FWD ", " FWD SINGAPORE", " AIA ", "GREAT EASTERN",
                   "NTUC INCOME", "SINGLIFE", "HL ASSURANCE"]),
    # " GIGA " needs both edges so GIGABYTE and GIGASPORTS are not phone bills.
    ("Bills & utilities", ["SINGTEL", "STARHUB", " M1 ", " AXS ", " AXS PTE", " SP SERVICES",
                           " SP DIGITAL", " SIMBA", " CIRCLES", " GIGA ", "OPEN.GOV.SG"]),
    ("Travel", ["AIRLINE", "SINGAPOREAIR", " SCOOT", "JETSTAR", "AGODA", "BOOKING.COM", "AIRBNB",
                "KLOOK", "KKDAY", "ROTTNEST EXPRESS", "TRIP.COM", " HOTEL",
                "USCUSTOMS ESTA", "IVISA SERVICES", "IMMIGRATION CANADA", "AUSTRALIANETA"]),
]

# Within Games, which storefront or publisher the charge belongs to. Statements
# name the biller, not the title, so this is the finest grain available.
# game_of() anchors patterns the same way categorize() does, which is why the
# three-letter " PSN" and " G2A" carry a leading space.
GAME_RULES = [
    ("HoYoverse", ["HOYOVERSE", "COGNOSPHERE", "MIHOYO"]),
    ("Kuro Games", ["KURO GAMES"]),
    ("Steam", ["STEAMGAMES"]),
    ("G2G marketplace", ["G2G.COM"]),
    ("ZeusX marketplace", ["ZEUSX"]),
    ("PlayStation", ["PLAYSTATION", " PSN"]),
    ("Nintendo", ["NINTENDO"]),
    ("Xbox", ["XBOX"]),
    ("Epic Games", ["EPIC GAMES"]),
    ("Riot Games", ["RIOT GAMES", "RIOTGAMES", "RIOT*"]),
    ("Garena", ["GARENA"]),
    ("Roblox", ["ROBLOX"]),
    # Billed as "MEP*bahjasuq"/"bahjasuq"; MEP* is the processor, bahjasuq the storefront.
    ("Chaos Zero Nightmare", ["BAHJASUQ"]),
    ("Top-up sites", ["CODASHOP", "UNIPIN", "SEA GAMER", " G2A"]),
]


def load(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def manual(name, default=None):
    return load(os.path.join(MANUAL_DIR, name), default)


def padded(description):
    """The description as the rule tables expect to see it.

    Whitespace is collapsed and one space is added at each end, so a pattern
    carrying an explicit edge space only matches at a word edge. Without this a
    bare "GYM" matched GYMBOREE and a bare "M1 " matched BLOOM1 SHOP.
    """
    return " " + " ".join(description.split()).upper() + " "


def categorize(description):
    matches = category_matches(description)
    return matches[0] if matches else "Other"


def category_matches(description):
    """Return every matching rule category, preserving rule precedence."""
    d = padded(description)
    matches = []
    for category, patterns in CATEGORY_RULES:
        for p in patterns:
            if p in d:
                matches.append(category)
                break
    return matches


def has_category_override(overrides, transaction_id):
    override = overrides.get(transaction_id)
    return isinstance(override, dict) and bool(override.get("category"))


def is_foodpanda_description(description):
    value = padded(description)
    return "FOODPANDA" in value or "FOOD PANDA" in value or "FP*FOOD" in value


def prepare_foodpanda_orders(order_data, card_rows):
    """Validate browser-extracted orders and link only unambiguous card rows.

    Foodpanda's displayed total can differ from the amount charged to the card
    (wallet credit, vouchers, or another payment method). A link is therefore
    made only when the transaction date and amount agree exactly and the number
    of orders equals the number of statement rows for that key. Anything else
    remains visible as an unmatched order instead of being guessed.
    """
    raw_orders = order_data.get("orders", []) if isinstance(order_data, dict) else []
    if not isinstance(raw_orders, list):
        raise SystemExit("manual/foodpanda_orders.json orders must be a list")

    orders = []
    seen = set()
    for index, raw in enumerate(raw_orders, 1):
        label = "Foodpanda order %d" % index
        if not isinstance(raw, dict):
            raise SystemExit("%s must be an object" % label)
        order_id = raw.get("orderId")
        if not isinstance(order_id, str) or not re.match(
                r"^[a-z0-9]{4}-[0-9]{4}-[a-z0-9]{4}$", order_id, re.I):
            raise SystemExit("%s has invalid orderId" % label)
        if order_id in seen:
            raise SystemExit("Foodpanda orderId %s is duplicated" % order_id)
        seen.add(order_id)
        date_value = raw.get("date")
        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise SystemExit("%s has invalid date" % label)
        time_value = raw.get("time")
        try:
            datetime.strptime(time_value, "%H:%M")
        except (TypeError, ValueError):
            raise SystemExit("%s has invalid time" % label)
        merchant = raw.get("merchant")
        if not isinstance(merchant, str) or not merchant.strip():
            raise SystemExit("%s has no merchant" % label)
        try:
            amount = round(float(raw.get("amount")), 2)
        except (TypeError, ValueError):
            raise SystemExit("%s has invalid amount" % label)
        if amount < 0:
            raise SystemExit("%s has a negative amount" % label)
        fulfilment = raw.get("fulfillment")
        if fulfilment not in FOODPANDA_FULFILMENT:
            raise SystemExit("%s has invalid fulfillment" % label)
        derived_category = (
            "Groceries" if re.match(r"^pandamart\b", merchant.strip(), re.I)
            else "Food & dining"
        )
        supplied_category = raw.get("category", derived_category)
        if supplied_category not in FOODPANDA_CATEGORIES or supplied_category != derived_category:
            raise SystemExit("%s category disagrees with its merchant" % label)
        orders.append({
            "orderId": order_id.lower(),
            "date": date_value,
            "time": time_value,
            "fulfillment": fulfilment,
            "merchant": merchant.strip(),
            "amount": amount,
            "category": derived_category,
        })

    order_groups = {}
    for order in orders:
        key = (order["date"], int(round(order["amount"] * 100)))
        order_groups.setdefault(key, []).append(order)
    card_groups = {}
    for row in card_rows:
        if row.get("credit") or not is_foodpanda_description(row.get("description", "")):
            continue
        key = (row.get("date"), int(round(float(row.get("amount", 0)) * 100)))
        card_groups.setdefault(key, []).append(row)

    by_transaction = {}
    for key, grouped_orders in order_groups.items():
        grouped_rows = card_groups.get(key, [])
        if len(grouped_orders) != len(grouped_rows):
            continue
        for order, row in zip(
                sorted(grouped_orders, key=lambda item: item["orderId"]),
                sorted(grouped_rows, key=lambda item: item["id"])):
            order["statementTransactionId"] = row["id"]
            by_transaction[row["id"]] = order

    orders.sort(key=lambda item: (item["date"], item["time"], item["orderId"]), reverse=True)
    return orders, by_transaction


def is_shopee_description(description):
    return "SHOPEE" in padded(description)


def prepare_shopee_orders(order_data, card_rows):
    """Validate Shopee orders and link only equal-cardinality amount groups."""
    raw_orders = order_data.get("orders", []) if isinstance(order_data, dict) else []
    if not isinstance(raw_orders, list):
        raise SystemExit("manual/shopee_orders.json orders must be a list")
    start = order_data.get("statementFrom")
    through = order_data.get("statementThrough")
    match_history_limit = order_data.get("statementOrderMaxHistoryIndex")
    try:
        datetime.strptime(start, "%Y-%m-%d")
        datetime.strptime(through, "%Y-%m-%d")
    except (TypeError, ValueError):
        if raw_orders:
            raise SystemExit("Shopee statement date window is invalid")
        start = through = None
    if (match_history_limit is not None
            and (not isinstance(match_history_limit, int) or match_history_limit < 0)):
        raise SystemExit("Shopee statement history cutoff is invalid")

    orders = []
    seen = set()
    seen_history_indexes = set()
    for index, raw in enumerate(raw_orders, 1):
        label = "Shopee order %d" % index
        if not isinstance(raw, dict):
            raise SystemExit("%s must be an object" % label)
        order_id = raw.get("orderId")
        if not isinstance(order_id, str) or not re.fullmatch(r"\d{10,15}", order_id):
            raise SystemExit("%s has invalid orderId" % label)
        if order_id in seen:
            raise SystemExit("Shopee orderId %s is duplicated" % order_id)
        seen.add(order_id)
        merchant = raw.get("merchant")
        status = raw.get("status")
        if not isinstance(merchant, str) or not merchant.strip():
            raise SystemExit("%s has no merchant" % label)
        if status not in SHOPEE_STATUSES:
            raise SystemExit("%s has invalid status" % label)
        try:
            amount = round(float(raw.get("amount")), 2)
        except (TypeError, ValueError):
            raise SystemExit("%s has invalid amount" % label)
        if amount < 0:
            raise SystemExit("%s has invalid amount" % label)
        items = raw.get("items", [])
        if (not isinstance(items, list)
                or any(not isinstance(item, str) or not item.strip() for item in items)):
            raise SystemExit("%s has invalid items" % label)
        history_index = raw.get("historyIndex", index - 1)
        if not isinstance(history_index, int) or history_index < 0:
            raise SystemExit("%s has invalid historyIndex" % label)
        if history_index in seen_history_indexes:
            raise SystemExit("Shopee historyIndex %s is duplicated" % history_index)
        seen_history_indexes.add(history_index)
        orders.append({
            "orderId": order_id,
            "merchant": merchant.strip(),
            "status": status,
            "amount": amount,
            "items": [item.strip() for item in items],
            "historyIndex": history_index,
            "category": (
                "Groceries" if merchant.strip().lower() == "shopee supermarket"
                else "Shopping"
            ),
        })

    order_groups = {}
    for order in orders:
        if (match_history_limit is not None
                and order["historyIndex"] > match_history_limit):
            continue
        order_groups.setdefault(int(round(order["amount"] * 100)), []).append(order)
    card_groups = {}
    for row in card_rows:
        row_date = row.get("date")
        if (row.get("credit") or not is_shopee_description(row.get("description", ""))
                or not row_date or not start or row_date < start or row_date > through):
            continue
        card_groups.setdefault(int(round(float(row.get("amount", 0)) * 100)), []).append(row)

    by_transaction = {}
    for cents, grouped_orders in order_groups.items():
        grouped_rows = card_groups.get(cents, [])
        if not grouped_orders or len(grouped_orders) != len(grouped_rows):
            continue
        for order, row in zip(
                sorted(grouped_orders, key=lambda item: item["historyIndex"]),
                sorted(grouped_rows, key=lambda item: (item["date"], item["id"]), reverse=True)):
            order["statementTransactionId"] = row["id"]
            order["date"] = row["date"]
            by_transaction[row["id"]] = order
    orders.sort(key=lambda item: item["historyIndex"])
    return orders, by_transaction


def is_grab_description(description):
    value = str(description or "").strip().upper()
    return (bool(re.match(r"^GRAB(?:\*|\s|-)", value))
            and not value.startswith("SUBSCRIPTIONGRAB"))


def grab_location_label(location, aliases=None):
    """Return a private friendly name when a configured address fragment matches."""
    original = str(location or "").strip()
    normalized = " ".join(original.upper().split())
    aliases = aliases or {}
    if not isinstance(aliases, dict):
        raise SystemExit("identity grabLocationAliases must be an object")
    for address, label in aliases.items():
        address = " ".join(str(address or "").upper().split())
        if address and address in normalized and isinstance(label, str) and label.strip():
            return label.strip()
    return original


def merge_grab_web_history(receipt_data, history_data):
    """Merge Grab's six-month web history into richer Gmail receipt data.

    Booking codes are stable across both sources. Gmail remains authoritative
    for item lines, payment method, and receipt total; the web export supplies
    explicit Personal/Business profile, route, service, and otherwise-missing
    bookings. A conflicting web amount is retained separately for review.
    """
    raw_receipts = (
        receipt_data.get("receipts", []) if isinstance(receipt_data, dict) else []
    )
    if not isinstance(raw_receipts, list):
        raise SystemExit("manual/grab_receipts.json receipts must be a list")
    merged = [dict(item) if isinstance(item, dict) else item for item in raw_receipts]
    for item in merged:
        if isinstance(item, dict):
            item.setdefault("webHistoryAmount", None)
            item.setdefault("webHistoryAmountDiffers", False)
            item.setdefault("evidenceSources", ["Gmail receipt"])
    by_id = {
        item.get("receiptId"): item for item in merged
        if isinstance(item, dict) and item.get("receiptId")
    }

    fields = history_data.get("fields", []) if isinstance(history_data, dict) else []
    records = history_data.get("records", []) if isinstance(history_data, dict) else []
    if not records:
        return {"receipts": merged}, {"records": 0, "added": 0, "enriched": 0}
    if not isinstance(fields, list) or not isinstance(records, list):
        raise SystemExit("manual/grab_web_history.json has invalid fields or records")

    added = enriched = 0
    for index, values in enumerate(records, 1):
        if not isinstance(values, list) or len(values) != len(fields):
            raise SystemExit("Grab web history record %d has invalid columns" % index)
        raw = dict(zip(fields, values))
        booking_code = str(raw.get("bookingCode") or "").strip()
        if not booking_code:
            raise SystemExit("Grab web history record %d has no booking code" % index)
        try:
            occurred = datetime.strptime(raw.get("dateTime"), "%d %b %Y, %I:%M%p")
            history_amount = round(float(raw.get("amount")), 2)
        except (TypeError, ValueError):
            raise SystemExit("Grab web history record %d has invalid date or amount" % index)
        profile = str(raw.get("profile") or "").strip().lower()
        if profile not in ("personal", "business"):
            raise SystemExit("Grab web history record %d has invalid profile" % index)
        service = str(raw.get("fleetType") or "Grab").strip()
        service_key = service.lower()
        if "food" in service_key:
            category = "Food & dining"
        elif "mart" in service_key:
            category = "Groceries"
        else:
            category = "Transport"
        pickup = str(raw.get("pickup") or "").strip()
        dropoff = str(raw.get("dropoff") or "").strip()
        merchant = pickup if category in ("Food & dining", "Groceries") else "Grab"
        corporate = profile == "business"

        existing = by_id.get(booking_code)
        if existing is not None:
            existing["profile"] = profile
            existing["corporate"] = corporate
            existing["eligibleForPersonalFinance"] = profile == "personal"
            if not existing.get("time"):
                existing["time"] = occurred.strftime("%H:%M")
            if not existing.get("service") or existing.get("service") == "Grab":
                existing["service"] = service
            if not existing.get("pickup"):
                existing["pickup"] = pickup
            if not existing.get("dropoff"):
                existing["dropoff"] = dropoff
            if (not existing.get("merchant") or existing.get("merchant") == "Grab") and merchant != "Grab":
                existing["merchant"] = merchant
            existing["webHistoryAmount"] = history_amount
            existing["webHistoryAmountDiffers"] = (
                abs(round(float(existing.get("amount")), 2) - history_amount) > 0.01
            )
            existing["evidenceSources"] = ["Gmail receipt", "Grab web history"]
            enriched += 1
            continue

        synthesized = {
            "receiptId": booking_code,
            "date": occurred.strftime("%Y-%m-%d"),
            "time": occurred.strftime("%H:%M"),
            "service": service,
            "category": category,
            "amount": history_amount,
            "currency": str(raw.get("currency") or "").strip().upper(),
            "profile": profile,
            "corporate": corporate,
            "eligibleForPersonalFinance": profile == "personal",
            "merchant": merchant,
            "items": [],
            "pickup": pickup,
            "dropoff": dropoff,
            "paymentMethod": "",
            "webHistoryAmount": history_amount,
            "webHistoryAmountDiffers": False,
            "evidenceSources": ["Grab web history"],
        }
        merged.append(synthesized)
        by_id[booking_code] = synthesized
        added += 1

    return {"receipts": merged}, {
        "records": len(records), "added": added, "enriched": enriched,
    }


def prepare_grab_receipts(receipt_data, card_rows, location_aliases=None):
    """Validate Grab receipts and match only strongly supported statement charges.

    Exact receipt references and unique direct-card charges are matched first.
    Remaining GrabPay wallet funding is commonly rounded or split across card
    rows, so a receipt amount is not expected to equal one statement row. Those
    rows link only when the full same-day receipt and card groups reconcile
    within S$1.50 and agree on category and corporate status. Unknown profiles
    never match. Corporate receipts may link for auditability but are categorized
    as Payment so they stay out of personal spending totals.
    """
    raw_receipts = (
        receipt_data.get("receipts", []) if isinstance(receipt_data, dict) else []
    )
    if not isinstance(raw_receipts, list):
        raise SystemExit("manual/grab_receipts.json receipts must be a list")
    receipts = []
    seen = set()
    for index, raw in enumerate(raw_receipts, 1):
        label = "Grab receipt %d" % index
        if not isinstance(raw, dict):
            raise SystemExit("%s must be an object" % label)
        receipt_id = raw.get("receiptId")
        if not isinstance(receipt_id, str) or not receipt_id.strip():
            raise SystemExit("%s has invalid receiptId" % label)
        if receipt_id in seen:
            raise SystemExit("Grab receiptId %s is duplicated" % receipt_id)
        seen.add(receipt_id)
        date_value = raw.get("date")
        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise SystemExit("%s has invalid date" % label)
        time_value = raw.get("time", "")
        if time_value:
            try:
                datetime.strptime(time_value, "%H:%M")
            except (TypeError, ValueError):
                raise SystemExit("%s has invalid time" % label)
        try:
            amount = round(float(raw.get("amount")), 2)
        except (TypeError, ValueError):
            raise SystemExit("%s has invalid amount" % label)
        if amount < 0:
            raise SystemExit("%s has negative amount" % label)
        category = raw.get("category")
        profile = str(raw.get("profile", "unknown")).lower()
        if category not in GRAB_CATEGORIES:
            raise SystemExit("%s has invalid category" % label)
        if profile not in GRAB_PROFILES:
            raise SystemExit("%s has invalid profile" % label)
        corporate = profile in ("business", "corporate")
        if bool(raw.get("corporate")) != corporate:
            raise SystemExit("%s corporate flag disagrees with profile" % label)
        eligible = profile == "personal"
        if bool(raw.get("eligibleForPersonalFinance")) != eligible:
            raise SystemExit(
                "%s personal-finance eligibility disagrees with profile" % label
            )
        items = raw.get("items", [])
        if (not isinstance(items, list)
                or any(not isinstance(item, str) or not item.strip() for item in items)):
            raise SystemExit("%s has invalid items" % label)
        pickup = str(raw.get("pickup") or "").strip()
        dropoff = str(raw.get("dropoff") or "").strip()
        receipt = {
            "receiptId": receipt_id.strip(),
            "date": date_value,
            "time": time_value,
            "service": str(raw.get("service") or "Grab").strip(),
            "category": category,
            "amount": amount,
            "currency": str(raw.get("currency") or "").upper(),
            "profile": profile,
            "corporate": corporate,
            "eligibleForPersonalFinance": eligible,
            "merchant": str(raw.get("merchant") or "Grab").strip(),
            "items": [item.strip() for item in items],
            "pickup": pickup,
            "dropoff": dropoff,
            "pickupLabel": grab_location_label(pickup, location_aliases),
            "dropoffLabel": grab_location_label(dropoff, location_aliases),
            "paymentMethod": str(raw.get("paymentMethod") or "").strip(),
            "webHistoryAmount": raw.get("webHistoryAmount"),
            "webHistoryAmountDiffers": bool(raw.get("webHistoryAmountDiffers")),
            "evidenceSources": list(raw.get("evidenceSources", [])),
        }
        receipts.append(receipt)

    by_transaction = {}
    matched_receipt_ids = set()
    matched_row_ids = set()

    def link(grouped_receipts, grouped_rows):
        for receipt in grouped_receipts:
            receipt.setdefault("statementTransactionIds", [])
            for row in grouped_rows:
                if row["id"] not in receipt["statementTransactionIds"]:
                    receipt["statementTransactionIds"].append(row["id"])
                by_transaction.setdefault(row["id"], []).append(receipt)

    eligible_receipts = [
        receipt for receipt in receipts
        if receipt["profile"] != "unknown" and receipt["currency"] == "SGD"
    ]
    eligible_rows = [
        row for row in card_rows
        if not row.get("credit") and is_grab_description(row.get("description"))
    ]

    def attach_one(receipt, row):
        link([receipt], [row])
        matched_receipt_ids.add(receipt["receiptId"])
        matched_row_ids.add(row["id"])

    # An exact receipt reference is stronger evidence than the daily totals and
    # must survive an unrelated Grab charge on the same date.
    for receipt in eligible_receipts:
        receipt_id = receipt["receiptId"].upper()
        candidates = [
            row for row in eligible_rows
            if row["id"] not in matched_row_ids
            and row.get("date") == receipt["date"]
            and receipt_id in str(row.get("description") or "").upper()
            and abs(float(row.get("amount", 0)) - receipt["amount"]) <= 0.01
        ]
        if len(candidates) == 1:
            attach_one(receipt, candidates[0])

    def direct_card_payment(receipt):
        method = receipt["paymentMethod"].strip().upper()
        return bool(method) and not any(value in method for value in (
            "GRABPAY", "PAYLATER", "EVERYONE PAYS",
        ))

    # Older direct-card receipts may omit their ID from the statement. Link only
    # a unique same-date, exact-amount pair; competing candidates remain open.
    proposals = {}
    for receipt in eligible_receipts:
        if (receipt["receiptId"] in matched_receipt_ids
                or not direct_card_payment(receipt)):
            continue
        candidates = [
            row for row in eligible_rows
            if row["id"] not in matched_row_ids
            and row.get("date") == receipt["date"]
            and abs(float(row.get("amount", 0)) - receipt["amount"]) <= 0.01
        ]
        if len(candidates) == 1:
            proposals.setdefault(candidates[0]["id"], []).append(
                (receipt, candidates[0]))
    for pairs in proposals.values():
        if len(pairs) == 1:
            attach_one(*pairs[0])

    receipt_groups = {}
    for receipt in eligible_receipts:
        if receipt["receiptId"] not in matched_receipt_ids:
            receipt_groups.setdefault(receipt["date"], []).append(receipt)
    card_groups = {}
    for row in eligible_rows:
        if row["id"] not in matched_row_ids:
            card_groups.setdefault(row.get("date"), []).append(row)

    for date_value, grouped_receipts in receipt_groups.items():
        grouped_rows = card_groups.get(date_value, [])
        if not grouped_rows:
            continue
        if len({receipt["category"] for receipt in grouped_receipts}) != 1:
            continue
        if len({receipt["corporate"] for receipt in grouped_receipts}) != 1:
            continue
        receipt_total = sum(receipt["amount"] for receipt in grouped_receipts)
        card_total = sum(float(row.get("amount", 0)) for row in grouped_rows)
        if abs(receipt_total - card_total) > 1.50:
            continue
        if len(grouped_receipts) == len(grouped_rows):
            for receipt, row in zip(
                    sorted(grouped_receipts, key=lambda item: (item["amount"], item["receiptId"])),
                    sorted(grouped_rows, key=lambda item: (float(item.get("amount", 0)), item["id"]))):
                link([receipt], [row])
        elif len(grouped_receipts) == 1:
            link(grouped_receipts, grouped_rows)
        elif len(grouped_rows) == 1:
            link(grouped_receipts, grouped_rows)

    receipts.sort(
        key=lambda item: (item["date"], item["time"], item["receiptId"]), reverse=True
    )
    return receipts, by_transaction


def prepare_insurance(insurance_data):
    """Validate policies and derive comparable annual premium and coverage totals."""
    if not isinstance(insurance_data, dict):
        raise SystemExit("manual/insurance.json must be an object")
    people = insurance_data.get("people", [])
    if not isinstance(people, list):
        raise SystemExit("manual/insurance.json people must be a list")
    output_people = []
    seen_people = set()
    seen_policies = set()
    combined = {
        "policies": 0, "activePolicies": 0, "maturedPolicies": 0,
        "lapsedPolicies": 0, "annualCashPremium": 0, "annualCpfPremium": 0,
        "monthlyEquivalent": 0,
    }

    def money(value, label):
        try:
            amount = round(float(value or 0), 2)
        except (TypeError, ValueError):
            raise SystemExit("%s must be a valid amount" % label)
        if not math.isfinite(amount) or amount < 0:
            raise SystemExit("%s must be a non-negative amount" % label)
        return amount

    def date_value(value, label):
        result = str(value or "").strip()
        if result:
            try:
                datetime.strptime(result, "%Y-%m-%d")
            except ValueError:
                raise SystemExit("%s must be a YYYY-MM-DD date" % label)
        return result

    for person_index, raw_person in enumerate(people, 1):
        label = "Insurance person %d" % person_index
        if not isinstance(raw_person, dict):
            raise SystemExit("%s must be an object" % label)
        person_id = str(raw_person.get("id") or "").strip().lower()
        name = str(raw_person.get("name") or "").strip()
        owner = str(raw_person.get("owner") or name).strip()
        if not person_id or not name or person_id in seen_people:
            raise SystemExit("%s has an invalid or duplicate identity" % label)
        seen_people.add(person_id)
        raw_policies = raw_person.get("policies", [])
        if not isinstance(raw_policies, list):
            raise SystemExit("%s policies must be a list" % label)
        coverage = {benefit: 0 for benefit in INSURANCE_BENEFITS}
        policies = []
        totals = {
            "policies": 0, "activePolicies": 0, "maturedPolicies": 0,
            "lapsedPolicies": 0, "annualCashPremium": 0, "annualCpfPremium": 0,
            "monthlyEquivalent": 0,
        }
        for policy_index, raw in enumerate(raw_policies, 1):
            policy_label = "%s policy %d" % (name, policy_index)
            if not isinstance(raw, dict):
                raise SystemExit("%s must be an object" % policy_label)
            policy_id = str(raw.get("id") or "").strip()
            if not policy_id or policy_id in seen_policies:
                raise SystemExit("%s has an invalid or duplicate id" % policy_label)
            seen_policies.add(policy_id)
            company = str(raw.get("company") or "").strip()
            plan = str(raw.get("plan") or "").strip()
            if not company or not plan:
                raise SystemExit("%s needs a company and plan" % policy_label)
            start_date = date_value(raw.get("startDate"), policy_label + " start date")
            status = str(raw.get("status") or "In Force").strip().title()
            if status not in ("In Force", "Matured", "Lapsed"):
                raise SystemExit("%s has an unsupported status" % policy_label)
            premium_data = raw.get("premiums", {})
            if not isinstance(premium_data, dict):
                raise SystemExit("%s premiums must be an object" % policy_label)
            frequency = str(premium_data.get("frequency") or "").strip().title()
            if frequency not in ("", "Monthly", "Annual"):
                raise SystemExit("%s has an unsupported payment frequency" % policy_label)
            cash_with = money(premium_data.get("cashWithValue"), policy_label + " cash premium")
            cash_without = money(
                premium_data.get("cashWithoutValue"), policy_label + " protection premium")
            cpf_annual = money(premium_data.get("cpfAnnual"), policy_label + " CPF premium")
            multiplier = 12 if frequency == "Monthly" else 1
            annual_cash = round((cash_with + cash_without) * multiplier, 2)
            raw_benefits = raw.get("benefits", {})
            if not isinstance(raw_benefits, dict) or any(
                    benefit not in INSURANCE_BENEFITS for benefit in raw_benefits):
                raise SystemExit("%s has invalid benefits" % policy_label)
            benefits = {}
            for benefit in INSURANCE_BENEFITS:
                amount = money(raw_benefits.get(benefit), policy_label + " " + benefit)
                if amount:
                    benefits[benefit] = amount
                    if status == "In Force":
                        coverage[benefit] = round(coverage[benefit] + amount, 2)
            raw_valuation = raw.get("valuation", {})
            if not isinstance(raw_valuation, dict):
                raise SystemExit("%s valuation must be an object" % policy_label)
            valuation = {
                "asOf": date_value(raw_valuation.get("asOf"), policy_label + " valuation date"),
                "guaranteedBonus": money(raw_valuation.get("guaranteedBonus"), policy_label + " guaranteed bonus"),
                "grossSurrenderValue": money(raw_valuation.get("grossSurrenderValue"), policy_label + " gross surrender value"),
                "indebtedness": money(raw_valuation.get("indebtedness"), policy_label + " indebtedness"),
                "netSurrenderValue": money(raw_valuation.get("netSurrenderValue"), policy_label + " net surrender value"),
                "maturityValue": money(raw_valuation.get("maturityValue"), policy_label + " maturity value"),
            }
            raw_components = raw.get("components", [])
            if not isinstance(raw_components, list):
                raise SystemExit("%s components must be a list" % policy_label)
            components = []
            for component_index, component in enumerate(raw_components, 1):
                component_label = "%s component %d" % (policy_label, component_index)
                if not isinstance(component, dict) or not str(component.get("name") or "").strip():
                    raise SystemExit("%s needs a name" % component_label)
                components.append({
                    "name": str(component.get("name") or "").strip(),
                    "status": str(component.get("status") or "In Force").strip().title(),
                    "insuredPerson": str(component.get("insuredPerson") or "").strip(),
                    "relationship": str(component.get("relationship") or "").strip(),
                    "benefitLabel": str(component.get("benefitLabel") or "").strip(),
                    "sumAssured": money(component.get("sumAssured"), component_label + " sum assured"),
                    "premiumAmount": money(component.get("premiumAmount"), component_label + " premium"),
                    "premiumFrequency": str(
                        component.get("premiumFrequency") or "").strip().title(),
                    "coverageEffectiveDate": date_value(
                        component.get("coverageEffectiveDate"),
                        component_label + " coverage effective date"),
                    "premiumEndDate": date_value(component.get("premiumEndDate"), component_label + " premium end date"),
                    "coverExpiryDate": date_value(component.get("coverExpiryDate"), component_label + " cover expiry date"),
                    "nextDueDate": date_value(
                        component.get("nextDueDate"), component_label + " next due date"),
                    "paymentMethod": str(component.get("paymentMethod") or "").strip(),
                })
            raw_documents = raw.get("documents", [])
            if not isinstance(raw_documents, list):
                raise SystemExit("%s documents must be a list" % policy_label)
            documents = []
            for document_index, document in enumerate(raw_documents, 1):
                document_label = "%s document %d" % (policy_label, document_index)
                if not isinstance(document, dict) or not str(document.get("name") or "").strip():
                    raise SystemExit("%s needs a name" % document_label)
                documents.append({
                    "name": str(document.get("name") or "").strip(),
                    "type": str(document.get("type") or "").strip(),
                    "date": date_value(document.get("date"), document_label + " date"),
                })
            raw_coverage_notes = raw.get("coverageNotes", [])
            if not isinstance(raw_coverage_notes, list):
                raise SystemExit("%s coverage notes must be a list" % policy_label)
            raw_verification = raw.get("verification", {})
            if not isinstance(raw_verification, dict):
                raise SystemExit("%s verification must be an object" % policy_label)
            verification = {
                "source": str(raw_verification.get("source") or "").strip(),
                "checkedAt": date_value(
                    raw_verification.get("checkedAt"), policy_label + " verification date"),
            }
            if bool(verification["source"]) != bool(verification["checkedAt"]):
                raise SystemExit("%s verification needs both source and checkedAt" % policy_label)
            reconcile_with_statements = raw.get("reconcileWithImportedStatements", True)
            if not isinstance(reconcile_with_statements, bool):
                raise SystemExit(
                    "%s reconcileWithImportedStatements must be true or false" % policy_label)
            coverage_only = raw.get("coverageOnly", False)
            hidden_in_register = raw.get("hiddenInRegister", False)
            if not isinstance(coverage_only, bool):
                raise SystemExit("%s coverageOnly must be true or false" % policy_label)
            if not isinstance(hidden_in_register, bool):
                raise SystemExit("%s hiddenInRegister must be true or false" % policy_label)
            policy = {
                "id": policy_id,
                "personId": person_id,
                "company": company,
                "plan": plan,
                "policyNumber": str(raw.get("policyNumber") or "").strip(),
                "startDate": start_date,
                "status": status,
                "statusDate": date_value(raw.get("statusDate"), policy_label + " status date"),
                "type": str(raw.get("type") or "").strip(),
                "payableTerm": str(raw.get("payableTerm") or "").strip(),
                "premiumPaidToDate": date_value(raw.get("premiumPaidToDate"), policy_label + " premium paid-to date"),
                "premiumEndDate": date_value(raw.get("premiumEndDate"), policy_label + " premium end date"),
                "coverExpiryDate": date_value(raw.get("coverExpiryDate"), policy_label + " cover expiry date"),
                "paymentMethod": str(raw.get("paymentMethod") or "").strip(),
                "premiumPaidBy": str(raw.get("premiumPaidBy") or "").strip(),
                "reconcileWithImportedStatements": reconcile_with_statements,
                "coverageOnly": coverage_only,
                "hiddenInRegister": hidden_in_register,
                "portalPremiumTotal": money(
                    raw.get("portalPremiumTotal"), policy_label + " portal premium total"),
                "accountDebitAmount": money(
                    raw.get("accountDebitAmount"), policy_label + " account debit amount"),
                "faceValue": money(raw.get("faceValue"), policy_label + " face value"),
                "baseSumAssured": money(raw.get("baseSumAssured"), policy_label + " base sum assured"),
                "basicPremium": money(raw.get("basicPremium"), policy_label + " basic premium"),
                "multiplierBenefit": str(raw.get("multiplierBenefit") or "").strip(),
                "benefits": benefits,
                "coverageNotes": [str(note).strip() for note in raw_coverage_notes if str(note).strip()],
                "components": components,
                "valuation": valuation,
                "documents": documents,
                "verification": verification,
                "summary": str(raw.get("summary") or "").strip(),
                "premiumWaiver": str(raw.get("premiumWaiver") or "").strip(),
                "premiums": {
                    "cashWithValue": cash_with,
                    "cashWithoutValue": cash_without,
                    "cpfAnnual": cpf_annual,
                    "frequency": frequency,
                },
                "annualCashPremium": annual_cash,
                "monthlyEquivalent": round(annual_cash / 12, 2),
                "oneOffPaid": money(raw.get("oneOffPaid"), policy_label + " one-off payment"),
                "remarks": str(raw.get("remarks") or "").strip(),
            }
            policies.append(policy)
            if not coverage_only:
                totals["policies"] += 1
                if status == "In Force":
                    totals["activePolicies"] += 1
                elif status == "Matured":
                    totals["maturedPolicies"] += 1
                elif status == "Lapsed":
                    totals["lapsedPolicies"] += 1
                if status == "In Force":
                    totals["annualCashPremium"] += annual_cash
                    totals["annualCpfPremium"] += cpf_annual
        totals["annualCashPremium"] = round(totals["annualCashPremium"], 2)
        totals["annualCpfPremium"] = round(totals["annualCpfPremium"], 2)
        totals["monthlyEquivalent"] = round(totals["annualCashPremium"] / 12, 2)
        for key in combined:
            combined[key] += totals[key]
        output_people.append({
            "id": person_id, "name": name, "owner": owner, "coverage": coverage,
            "totals": totals, "policies": policies,
        })
    combined["annualCashPremium"] = round(combined["annualCashPremium"], 2)
    combined["annualCpfPremium"] = round(combined["annualCpfPremium"], 2)
    combined["monthlyEquivalent"] = round(combined["annualCashPremium"] / 12, 2)
    return {
        "source": str(insurance_data.get("source") or "").strip(),
        "sourceUrl": str(insurance_data.get("sourceUrl") or "").strip(),
        "extractedAt": str(insurance_data.get("extractedAt") or "").strip(),
        "premiumPolicy": str(insurance_data.get("premiumPolicy") or "").strip(),
        "totals": combined,
        "people": output_people,
    }


def game_of(description):
    d = padded(description)
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


CITY_SUFFIXES = (
    ("SINGAPORE",),
    ("PETALING", "JAYA"),
    ("JOHOR", "BAHRU"),
)


def _matches_city_suffix(tail, city):
    for index, token in enumerate(tail):
        expected = city[index]
        if index < len(tail) - 1:
            if token != expected:
                return False
            continue
        minimum = min(4, len(expected)) if index == 0 else 1
        if len(token) < minimum or not expected.startswith(token):
            return False
    return True


def _strip_trailing_noise(key):
    tokens = key.split()
    changed = True
    while changed and len(tokens) > 1:
        changed = False
        while len(tokens) > 1 and len(tokens[-1]) == 1:
            tokens.pop()
            changed = True
        for city in CITY_SUFFIXES:
            if changed:
                break
            longest = min(len(city), len(tokens) - 1)
            for take in range(longest, 0, -1):
                if _matches_city_suffix(tokens[-take:], city):
                    tokens = tokens[:-take]
                    changed = True
                    break
    return " ".join(tokens)


def legacy_merchant_key(description):
    s = description.upper()
    s = re.sub(r"GPC-[0-9A-Z]+", "", s)
    s = re.sub(r"[0-9]{4,}", "", s)
    s = re.sub(r"[^A-Z ]+", " ", s)
    return " ".join(s.split())[:26]


def merchant_key(description):
    """Canonical merchant identity shared with transaction-grouping.js."""
    original = str(description or "")
    key = original.upper()
    if re.match(r"^SUBSCRIPTIONGRAB(?:\*|\s|-|$)", key):
        return "GRAB SUBSCRIPTION"
    if re.match(r"^GRAB(?:\*|\s|-|$)", key):
        return "GRAB"
    if re.match(r"^NTUC\s+(?:FAIRPRICE\b|FP(?:\b|-))", key):
        return "NTUC FAIRPRICE"
    key = re.sub(r"GPC-[0-9A-Z]+", "", key)
    key = re.sub(
        r"\b(?:A-)?(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])[A-Z0-9]{8,}\b",
        "",
        key,
    )
    key = re.sub(r"[0-9]{4,}", "", key)
    key = re.sub(r"[^A-Z ]+", " ", key)
    key = re.sub(r"\b(?:SINGAPORE|PETALING JAYA|JOHOR BAHRU)\b", " ", key)
    key = _strip_trailing_noise(" ".join(key.split()))
    return key or original.upper().strip()


def main():
    cards = load(CARDS_PATH)
    if not cards:
        raise SystemExit("No card_transactions.json - run scripts/parse_cc.py first")

    rows = list(cards.get("transactions", []))
    identity_file = manual("identity.json", {})
    insurance = prepare_insurance(manual("insurance.json", {"people": []}))
    foodpanda_orders, foodpanda_by_transaction = prepare_foodpanda_orders(
        manual("foodpanda_orders.json", {"orders": []}), rows
    )
    shopee_orders, shopee_by_transaction = prepare_shopee_orders(
        manual("shopee_orders.json", {"orders": []}), rows
    )
    grab_source, grab_history_stats = merge_grab_web_history(
        manual("grab_receipts.json", {"receipts": []}),
        manual("grab_web_history.json", {"fields": [], "records": []}),
    )
    grab_receipts, grab_by_transaction = prepare_grab_receipts(
        grab_source, rows, identity_file.get("grabLocationAliases") or {}
    )
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
        keys = (merchant_key(description), legacy_merchant_key(description))
        return any(p in key for p in confirmed_patterns for key in keys)

    # Repeat charges share a tag key, so hand out that key's owners one per row.
    # Rows are processed in a stable order, so the same row gets the same owner
    # on every build.
    rows.sort(key=lambda r: (r["month"], r.get("date") or "", r["description"], r["amount"]))
    tag_cursor = {}

    transactions = []
    tagged_id = tagged_exact = tagged_rule = 0
    for r in rows:
        rule_categories = category_matches(r["description"])
        rule_category = rule_categories[0] if rule_categories else "Other"
        override = transaction_overrides.get(r["id"], {})
        foodpanda_order = foodpanda_by_transaction.get(r["id"])
        shopee_order = shopee_by_transaction.get(r["id"])
        grab_matches = grab_by_transaction.get(r["id"], [])
        grab_statement = is_grab_description(r["description"])
        grab_unreconciled = grab_statement and not grab_matches
        grab_corporate = bool(grab_matches and grab_matches[0]["corporate"])
        grab_category = grab_matches[0]["category"] if grab_matches else None
        inferred_category = (
            "Payment" if grab_corporate
            else foodpanda_order["category"] if foodpanda_order
            else shopee_order["category"] if shopee_order
            else grab_category if grab_matches
            else "Wallet funding" if grab_unreconciled
            else rule_category
        )
        category = override.get("category", inferred_category)
        category_source = (
            "manual-override" if override.get("category")
            else "grab-corporate" if grab_corporate
            else "foodpanda-order" if foodpanda_order
            else "shopee-order" if shopee_order
            else "grab-receipt" if grab_matches
            else "grab-unreconciled" if grab_unreconciled
            else "merchant-rule"
        )
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
                owner = (owner_rules.get(merchant_key(r["description"]))
                         or owner_rules.get(legacy_merchant_key(r["description"])))
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
            "merchantKey": merchant_key(r["description"]),
            "amount": r["amount"],
            "type": tx_type,
            "owner": owner or "Untagged",
            "ownerSource": owner_source,
            "category": category,
            "categorySource": category_source,
            "ruleCategory": rule_category,
            "ruleCategories": rule_categories,
            "provenance": r["provenance"],
        }
        if r.get("foreign"):
            record["foreign"] = r["foreign"]
        if foodpanda_order:
            record["foodpanda"] = {
                key: foodpanda_order[key]
                for key in ("orderId", "date", "time", "fulfillment", "merchant", "amount")
            }
        if shopee_order:
            record["shopee"] = {
                key: shopee_order[key]
                for key in (
                    "orderId", "merchant", "status", "amount", "items", "historyIndex"
                )
            }
        if grab_statement:
            record["grab"] = {
                "status": "receipt-matched" if grab_matches else "unreconciled",
                "kind": "receipt" if grab_matches else "wallet-funding",
                "corporate": grab_corporate,
                "receipts": [
                    {
                        key: receipt[key]
                        for key in (
                            "receiptId", "date", "time", "service", "category",
                            "amount", "currency", "profile", "corporate", "merchant",
                            "items", "pickup", "dropoff", "paymentMethod",
                            "pickupLabel", "dropoffLabel",
                            "webHistoryAmount", "webHistoryAmountDiffers",
                            "evidenceSources",
                        )
                    }
                    for receipt in grab_matches
                ],
            }
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
    overlap_rows = [
        t for t in review_rows
        if len(t.get("ruleCategories", [])) > 1
        and not has_category_override(transaction_overrides, t["id"])
    ]
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
    generation_id = cards.get("generationId")
    account_generation = account_data.get("generationId")
    if generation_id and account_generation and generation_id != account_generation:
        raise SystemExit(
            "Card and account data belong to different import generations; "
            "run scripts/import_all.py."
        )
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
        "status": "review" if (untagged_rows or other_rows or overlap_rows or unverified_rows
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
            "categoryRuleOverlap": {
                "count": len(overlap_rows),
                "amount": round(sum(abs(t["amount"]) for t in overlap_rows), 2),
                "ids": [t["id"] for t in overlap_rows],
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
        "foodpanda": {
            "orders": len(foodpanda_orders),
            "matched": len(foodpanda_by_transaction),
            "unmatched": len(foodpanda_orders) - len(foodpanda_by_transaction),
            "groceries": sum(
                1 for order in foodpanda_orders if order["category"] == "Groceries"
            ),
        },
        "shopee": {
            "orders": len(shopee_orders),
            "matched": len(shopee_by_transaction),
            "unmatched": len(shopee_orders) - len(shopee_by_transaction),
            "groceries": sum(
                1 for order in shopee_orders if order["category"] == "Groceries"
            ),
        },
        "grab": {
            "receipts": len(grab_receipts),
            "webHistoryRecords": grab_history_stats["records"],
            "webHistoryAdded": grab_history_stats["added"],
            "webHistoryEnriched": grab_history_stats["enriched"],
            "personal": sum(1 for receipt in grab_receipts if receipt["profile"] == "personal"),
            "corporate": sum(1 for receipt in grab_receipts if receipt["corporate"]),
            "unknownProfile": sum(
                1 for receipt in grab_receipts if receipt["profile"] == "unknown"
            ),
            "matched": sum(
                1 for receipt in grab_receipts if receipt.get("statementTransactionIds")
            ),
            "matchedTransactions": len(grab_by_transaction),
            "statementTransactions": sum(
                1 for row in rows
                if not row.get("credit") and is_grab_description(row.get("description"))
            ),
            "unreconciledTransactions": sum(
                1 for row in rows
                if (not row.get("credit")
                    and is_grab_description(row.get("description"))
                    and row["id"] not in grab_by_transaction)
            ),
            "corporateExcluded": sum(
                1 for receipt in grab_receipts
                if receipt["corporate"] and receipt.get("statementTransactionIds")
            ),
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
                "generationId": generation_id or account_generation,
                "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "months": months,
                "salarySteps": salary.get("steps", []),
                "salaryYears": salary.get("years", []),
                "gameSales": sales,
                "settlements": settlements,
                "identity": identity,
                "freshness": freshness,
                "quality": quality,
                "foodpandaOrders": foodpanda_orders,
                "shopeeOrders": shopee_orders,
                "grabReceipts": grab_receipts,
                "insurance": insurance,
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
    print("Foodpanda orders %d (%d matched to statement rows)"
          % (len(foodpanda_orders), len(foodpanda_by_transaction)))
    print("Shopee orders %d (%d matched to statement rows)"
          % (len(shopee_orders), len(shopee_by_transaction)))
    print("Grab receipts %d (%d matched receipts across %d statement rows; %d corporate excluded)"
          % (
              len(grab_receipts),
              sum(1 for receipt in grab_receipts if receipt.get("statementTransactionIds")),
              len(grab_by_transaction),
              sum(1 for receipt in grab_receipts
                  if receipt["corporate"] and receipt.get("statementTransactionIds")),
          ))
    print("Transaction checks: %d to review, %d recognized"
          % (risk_summary["count"], risk_summary["recognized"]))
    if filled:
        print("No statement for %s - used spreadsheet rows from legacy_transactions.json"
              % ", ".join(filled))


if __name__ == "__main__":
    main()
