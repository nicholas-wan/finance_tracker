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
import itertools
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
PROFILE_OWNER = "Yx"
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
# Published beside every automatic Shopee link so the drawer can say why the
# order is attached; the validator re-derives the same text.
SHOPEE_EXACT_MATCH_NOTE = "The order total uniquely matches this Shopee statement charge."
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
TRAVEL_CONTEXT_PATTERNS = ("ALIPAY", "WEIXIN", " SHANGHAI")

CATEGORY_RULES = [
    ("Payment", ["PAYMT THRU E-BANK"]),
    ("Rebates", ["CASH REBATE", "ADDITIONAL REBATE", "DEDUCTED UNI$",
                 "ONE CARD ENHANCED REBATE", "YUN-U SAS LIMITED"]),
    ("Work", ["PAYPROGLOBA"]),
    # High-confidence identities checked against public merchant/registry pages.
    ("Fees & charges", ["CARD MEMBERSHIP FEE", "LATE CHARGES", "CR LATE CHARGE", "CR INTEREST", "INTERESTS",
                        "MSF-ROM-NETS", "SPF EPDLP"]),
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
        "EAGLE EYE CENTRE",
        "JURONG POINT DENTAL",
    ]),
    ("Home & furnishings", ["AZORA CURTAIN", "BSH HOME APPLIANCES",
                            "THE FURNITURE BOUTIQUE", "FORTYTWO PTE LTD", "HOOGA"]),
    ("Entertainment", ["SISTIC", "DERTICKETSERVICE", "HAVE FUN - TPY",
                       "COW PLAY COW MOO", "CASH STUDIO", "SOLACE STUDIOS",
                       "KIOSK PLAY GROUP"]),
    ("Pet care", ["ANIMAL WORLD VET", "PAWSWING", "SMARTPAW", "MIUMIU PETSHOP", "PET TECH"]),
    ("Subscriptions", ["NETFLIX", "SPOTIFY", "YOUTUBE", "DISNEY", "ICLOUD", "GOOGLE ONE",
                       "SUBSCRIPTIONGRAB", "OPENAI", "ANTHROPIC", "PRIME VIDEO", "APPLE.COM/BILL",
                       "AMZNPRIMESG MEMBERSHI", "NAME-CHEAP.COM", "NORDPRODUCTS",
                       "PAYFORGE SERVICES", "GETGALATEA.COM", "FREEPIK PREMIUM"]),
    ("Transport", ["BUS/MRT", "BUS / MRT", "GRAB*", "WWW.TADA", "TADA.G", "GOJEK", "COMFORT",
                   "CDG TAXI", " SMRT", "UBER *TRIP", "MO.PLA", "GRAB RIDES", "GRAB-EC",
                   "CAUSEWAYLINK", "SP BUS AUNTY", " TADA ", "ALP*DIDI TAXI",
                   "LYFT CITI BIKE"]),
    ("Groceries", ["NTUC FAIRPRICE", "FAIRPRICE", "COLD STORAGE", "SHENG SIONG", " GIANT ",
                   "GIANT-", "NTUC FP-", "CHEERS HOLDINGS", "DON DON DONKI",
                   "PRIME SUPERMARKET", "BBQ WHOLESALE CENTRE", "CS FRESH", "JAYA GROCER",
                   "KAPITAN GROCERY", "7-ELEVEN", "7 ELEVEN", "ESSO-CHEERS", "LEE MART",
                   "NTUC FP ", "ACE DYNAMIC HOLDINGS"],
     # Vetoes: the supermarket token inside an unrelated business name.
     ["GIANT LEAP", "GIANT SWING", "GIANT CYCLE"]),
    ("Food & dining", ["FOOD PANDA", "FP*FOOD", "FOODPANDA", "KOPITIAM", "WOK N RICE", "URBAN GRILL",
                       "WATAMI", "SWENSEN", "FUN TOAST", "POULET", "SUSHI", "LLAO LLAO", "LUCKIN",
                       "DSTA DRINKS", "BOOST JUICE", "MCDONALD", " KFC", "STARBUCKS", "DELIVEROO",
                       "SHOPBACK", "RESTAURANT", " CAFE", "-CAFE", ".CAFE", "BAKERY", " TOAST",
                       " COFFEE", "-COFFEE", "FENG SHENG",
                       "LIHO TEA", " YHS", "WARBURG VENDING", "7-ELEVEN", "7 ELEVEN", "OLD CHANG KEE", "KIMLY", "ENCIK TAN",
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
                       "INCEPTION SG", "GRAIN", "LE PETIT CROISSANT", "YUNNANS", "BAKERSBREW", "ORANGE AND TEAL",
                       "HERBIVORE", "EL MESA", "CHIMICHANGA", "THAI KHA", "SCOOP WHOLEFOODS", "POCHA KOREAN",
                       "NANA'S GREEN TEA", "MAKAN PRATA", "RASAPURA", "PARIS BAGUETTE", "GYG ", "KRISPY KREME",
                       "SAAP SAAP", "PANDASNACKS", "F&B MANAGEMENT", "HE-BREW KOPI", "DAILY SCOOP", "FNB AHMENG",
                       "FOUR LEAVES", "COTTI+COFFEE", "KOPIFELLAS",
                       "SB125-AEON BUKIT INDAH", "AIF 111-ORH006", "ANDO.SG",
                       "RUHK PRIVATE LIMITED", "EAGLEWINGS LOFT", "SHABUSAI",
                       "GREENDOT", "WANG @ ARC", "IDATEN UDON", "QIN JI ROUGAMO",
                       "KURIYA FRESH FISH", "MARCHE 313", "KRAFTWICH",
                       "AN ACAI AFFAIR", "SANDO NTP", "POLAR PUFFS", "TEN TENTHS",
                       "NAM KEE PAU", "TAMJAI SAMGOR", "PLAYMADE", "NOMVNOM",
                       "TRH -ENTREPOT", "POUR.TRAITS", "MK LEVURE NATURELLE",
                       "THEATRE CONCESSIONS", "AC MERIDIAN PWCP", "SMP*ICE & TIME"],
     # Vetoes: cafe and food tokens inside furniture and appliance names.
     ["COFFEE TABLE", "COFFEE MACHINE", "COFFEE MAKER", "FOOD PROCESSOR", "BURGER PRESS"]),
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
                  " MR DIY", "TELECOM EQUIPMENT PL-WIRE", "MOMENTS SINGAPORE",
                  "VOSTELO.COM", "BHG SINGAPORE", "THE STYLE SOIREE", "SV STYLE HOUSE",
                  "DMK -", "TURTLE CHINATOWN", "LOTTE DUTY FREE",
                  "M & S - VIVO CITY"]),
    # " GYM " needs both edges: GYMBOREE is a children's brand, not a gym.
    ("Sports & fitness", ["MYACTIVESG", "ACTIVESG", "DECATHLON", " GYM ", "FITNESS",
                          "SPORTS DIRECT", "HELLO SPORTS", "GALA SPORTS", "ABSOLUTEYOU"]),
    ("Personal care", [" KCUTS", " SALON", " BARBER", " GUARDIAN", "WATSONS", "WATSON'S",
                       "VENUS BEAUTY", "HOCKHUA TONIC", "ZTP GINSENG", "G & G HAIR & BTY"]),
    ("Insurance", ["PRUDENTIAL", "TOKIO MARINE", " FWD ", " FWD SINGAPORE", " AIA ", "GREAT EASTERN",
                   "NTUC INCOME", "SINGLIFE", "HL ASSURANCE", "ETIQA INSURANCE"]),
    # " GIGA " needs both edges so GIGABYTE and GIGASPORTS are not phone bills.
    ("Bills & utilities", ["SINGTEL", "STARHUB", " M1 ", " AXS ", " AXS PTE", " SP SERVICES",
                           " SP DIGITAL", " SIMBA", " CIRCLES", " GIGA ", "MYREPUBLIC BROADBAND", "MYREPUBLIC LIMITED",
                           "OPEN.GOV.SG"]),
    ("Travel", ["AIRLINE", "SINGAPOREAIR", " SCOOT", "JETSTAR", "AGODA", "BOOKING.COM", "AIRBNB",
                "KLOOK", "KKDAY", "ROTTNEST EXPRESS", "TRIP.COM", "TRIP COM", "WWW TRIP COM", "BUSBUD", " HOTEL",
                "USCUSTOMS ESTA", "IVISA SERVICES", "IMMIGRATION CANADA", "AUSTRALIANETA",
                "RITZ CARLTON", "RIPLEYSCANA",
                "USEBOUNCE.COM", "UKVI ", "WUXILINGSHANJINGQU"]),
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


PARTNER_CHARGE_FIELDS = (
    "id", "sourceId", "date", "postedDate", "month", "description", "displayName",
    "amount", "type", "category", "foreign", "card", "ownerTag", "reason",
)


def prepare_partner_travel(raw):
    """Travel charges another tracker paid, copied by import_partner_travel.py.

    They are published beside the transactions, never inside them: the ledger,
    its totals and every source-to-output check stay this tracker's own money.
    The dashboard clusters them into trips and labels each as paid by the
    other person. Fails closed on anything malformed, like every manual file.
    """
    if not raw:
        return {"paidBy": "", "source": {}, "charges": []}
    if not isinstance(raw, dict):
        raise SystemExit("manual/partner_travel.json must be an object")
    paid_by = str(raw.get("paidBy") or "").strip()
    if not paid_by:
        raise SystemExit("manual/partner_travel.json needs paidBy")
    charges = raw.get("charges")
    if not isinstance(charges, list):
        raise SystemExit("manual/partner_travel.json charges must be a list")
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    seen = set()
    published = []
    for index, row in enumerate(charges, 1):
        label = "partner charge %d" % index
        if not isinstance(row, dict):
            raise SystemExit("%s must be an object" % label)
        tx_id = str(row.get("id") or "").strip()
        prefix = paid_by.lower() + "_"
        if not tx_id.startswith(prefix) or tx_id in seen:
            raise SystemExit("%s needs a unique id starting with %r" % (label, prefix))
        seen.add(tx_id)
        try:
            datetime.strptime(str(row.get("date")), "%Y-%m-%d")
        except (TypeError, ValueError):
            raise SystemExit("%s has an invalid date %r" % (label, row.get("date")))
        if not re.fullmatch(r"\d{4}-\d{2}", str(row.get("month") or "")):
            raise SystemExit("%s has an invalid month %r" % (label, row.get("month")))
        amount = row.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) \
                or not math.isfinite(amount) or amount <= 0:
            raise SystemExit("%s has an invalid amount %r" % (label, amount))
        if row.get("type") not in ("debit", "refund"):
            raise SystemExit("%s has an invalid type %r" % (label, row.get("type")))
        for field in ("description", "category"):
            if not str(row.get(field) or "").strip():
                raise SystemExit("%s has no %s" % (label, field))
        for field in ("displayName", "foreign", "card", "ownerTag", "reason", "sourceId", "postedDate"):
            if row.get(field) is not None and not isinstance(row.get(field), str):
                raise SystemExit("%s %s must be text" % (label, field))
        record = {field: row[field] for field in PARTNER_CHARGE_FIELDS if row.get(field) not in (None, "")}
        record["amount"] = round(float(amount), 2)
        published.append(record)
    published.sort(key=lambda record: (record["date"], record["id"]))
    return {
        "paidBy": paid_by,
        "source": {
            "tracker": str(source.get("tracker") or paid_by),
            "generationId": source.get("generationId"),
            "generatedAt": source.get("generatedAt"),
            "importedAt": source.get("importedAt"),
        },
        "charges": published,
    }


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
    """Return every matching rule category, preserving rule precedence.

    A rule may carry a third element: phrases that veto the match even though
    one of its tokens is present. " GIANT " marks the supermarket, but GIANT
    LEAP is a gym; " COFFEE" marks a cafe, but COFFEE TABLE is furniture.
    """
    d = padded(description)
    # These statement labels identify spending during the reviewed China trip.
    # The trip context intentionally outranks a nested taxi, cafe or shop name.
    if any(pattern in d for pattern in TRAVEL_CONTEXT_PATTERNS):
        return ["Travel"]
    matches = []
    for rule in CATEGORY_RULES:
        category, patterns = rule[0], rule[1]
        exclusions = rule[2] if len(rule) > 2 else ()
        if any(x in d for x in exclusions):
            continue
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


def is_trip_description(description):
    value = str(description or "").upper()
    return "TRIP.COM" in value or "TRIP COM" in value or "WWW TRIP COM" in value


def is_klook_description(description):
    return "KLOOK" in str(description or "").upper()


KLOOK_STATUSES = ("confirmed", "completed", "canceled", "expired")
# A ticket can be bought most of a year ahead of the activity and settles on
# the card within days; nothing later than a week after the activity is it.
KLOOK_MATCH_BEFORE_DAYS = 400
KLOOK_MATCH_AFTER_DAYS = 7


def prepare_klook_orders(order_data, card_rows, partner_charges=(), earliest_month=None):
    """Validate hand-captured Klook orders and link them to statement rows.

    The orders come from the account's bookings page, which shows the name,
    package, activity date, quantity, total paid and status of every order but
    not the day it was paid. So a charge links when exactly one payable order
    carries its SGD amount and the charge falls inside the booking window
    before the activity (exact); a refund when exactly one cancelled order does
    (refund); and a charge equal to the sum of two or three payable orders for
    the same activity date links to all of them (aggregate). Expired orders
    were never paid and are never linked. Returns the orders, the links keyed
    by row id, and the quality summary.
    """
    if not isinstance(order_data, dict) or not isinstance(order_data.get("orders", []), list):
        raise SystemExit("manual/klook_orders.json must hold an orders list")
    orders = []
    for index, raw in enumerate(order_data.get("orders", []), 1):
        label = "Klook order %d" % index
        if not isinstance(raw, dict):
            raise SystemExit("%s must be an object" % label)
        name = str(raw.get("name") or "").strip()
        if not name:
            raise SystemExit("%s has no name" % label)
        activity = str(raw.get("activityDate") or "").strip()
        try:
            datetime.strptime(activity, "%Y-%m-%d")
        except ValueError:
            raise SystemExit("%s has an invalid activityDate %r" % (label, raw.get("activityDate")))
        amount = raw.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) \
                or not math.isfinite(amount) or amount < 0:
            raise SystemExit("%s has an invalid amount %r" % (label, amount))
        currency = str(raw.get("currency") or "SGD").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise SystemExit("%s has an invalid currency" % label)
        status = str(raw.get("status") or "").strip().lower()
        if status not in KLOOK_STATUSES:
            raise SystemExit("%s has an unexpected status %r" % (label, raw.get("status")))
        for field in ("package", "quantity", "note"):
            if raw.get(field) is not None and not isinstance(raw.get(field), str):
                raise SystemExit("%s %s must be text" % (label, field))
        orders.append({
            "name": name,
            "package": str(raw.get("package") or "").strip(),
            "activityDate": activity,
            "quantity": str(raw.get("quantity") or "").strip(),
            "amount": round(float(amount), 2),
            "currency": currency,
            "status": status,
            "note": str(raw.get("note") or "").strip(),
        })

    def cents(value):
        return int(round(float(value) * 100))

    def in_window(row_date, activity):
        try:
            gap = (datetime.strptime(activity, "%Y-%m-%d")
                   - datetime.strptime(str(row_date), "%Y-%m-%d")).days
        except (TypeError, ValueError):
            return False
        return -KLOOK_MATCH_AFTER_DAYS <= gap <= KLOOK_MATCH_BEFORE_DAYS

    candidates = []
    for row in list(card_rows) + list(partner_charges):
        if not is_klook_description(row.get("description")):
            continue
        refund = bool(row.get("credit")) or row.get("type") == "refund"
        candidates.append({
            "id": row["id"], "date": row.get("date"), "amount": row.get("amount"), "refund": refund,
        })

    payable = [
        order for order in orders
        if order["currency"] == "SGD" and order["status"] != "expired" and order["amount"] > 0
    ]
    matches = {}
    used_charge = set()
    used_refund = set()
    for cand in candidates:
        if cand["refund"]:
            continue
        options = [
            i for i, order in enumerate(payable)
            if cents(order["amount"]) == cents(cand["amount"]) and in_window(cand["date"], order["activityDate"])
        ]
        rivals = [
            other for other in candidates
            if not other["refund"] and cents(other["amount"]) == cents(cand["amount"])
            and any(in_window(other["date"], payable[i]["activityDate"]) for i in options)
        ]
        if len(options) == 1 and len(rivals) == 1:
            used_charge.add(options[0])
            matches[cand["id"]] = {
                "kind": "exact", "orders": [payable[options[0]]],
                "note": "The order total uniquely matches this Klook charge.",
            }
    for cand in candidates:
        if not cand["refund"]:
            continue
        options = [
            i for i, order in enumerate(payable)
            if order["status"] == "canceled" and cents(order["amount"]) == cents(cand["amount"])
            and in_window(cand["date"], order["activityDate"])
        ]
        rivals = [
            other for other in candidates
            if other["refund"] and cents(other["amount"]) == cents(cand["amount"])
        ]
        if len(options) == 1 and len(rivals) == 1:
            used_refund.add(options[0])
            matches[cand["id"]] = {
                "kind": "refund", "orders": [payable[options[0]]],
                "note": "The refund equals the cancelled order's total.",
            }
    for cand in candidates:
        if cand["refund"] or cand["id"] in matches:
            continue
        free = [
            i for i, order in enumerate(payable)
            if i not in used_charge and in_window(cand["date"], order["activityDate"])
        ]
        found = []
        for size in (2, 3):
            for combo in itertools.combinations(free, size):
                if len({payable[i]["activityDate"] for i in combo}) != 1:
                    continue
                if sum(cents(payable[i]["amount"]) for i in combo) == cents(cand["amount"]):
                    found.append(combo)
        if len(found) == 1:
            used_charge.update(found[0])
            matches[cand["id"]] = {
                "kind": "aggregate", "orders": [payable[i] for i in found[0]],
                "note": "The charge equals the sum of %d Klook orders for the same day." % len(found[0]),
            }
    # An order older than the first statement cannot be awaiting one.
    def before_statements(order):
        return bool(earliest_month) and order["activityDate"][:7] < earliest_month
    awaiting = [
        {"name": order["name"], "activityDate": order["activityDate"],
         "amount": order["amount"], "status": order["status"]}
        for i, order in enumerate(payable)
        if order["status"] in ("confirmed", "completed") and i not in used_charge
        and not before_statements(order)
    ]
    stats = {
        "orders": len(orders),
        "payable": len(payable),
        "expired": sum(1 for order in orders if order["status"] == "expired"),
        "matchedCharges": sum(1 for match in matches.values() if match["kind"] != "refund"),
        "matchedRefunds": sum(1 for match in matches.values() if match["kind"] == "refund"),
        "matchedOrders": len(used_charge),
        "beforeStatements": sum(1 for order in payable if before_statements(order)),
        "awaiting": awaiting,
    }
    return orders, matches, stats


WECHAT_EVIDENCE_WINDOW_DAYS = 3
WALLET_STATUS_OK = ("支付成功",)


def prepare_wechat_payments(raw, card_rows, sgd_per_cny=None):
    """Read manual/wechat_payments.json, copied by import_wechat_statement.py.

    A payment on a tracked card is evidence for the statement row carrying
    the same CNY amount within a few days. A payment on any other card is
    spending the statements never show: it is published beside them as paid
    via that card with an SGD estimate, since the card's own conversion is
    not on hand. The estimate uses the rate of this tracker's nearest CNY
    charge by date, or the sgdPerCny the file states, and is marked as such.
    Returns (wallet charges, evidence links by row id, quality summary).
    """
    if not raw:
        return [], {}, {"payments": 0, "wallet": 0, "evidence": 0, "unmappedMethods": [],
                        "cnyTotal": 0.0, "sgdEstimate": 0.0, "rate": None}
    if not isinstance(raw, dict) or not isinstance(raw.get("payments"), list):
        raise SystemExit("manual/wechat_payments.json must hold a payments list")
    instruments = raw.get("instruments") if isinstance(raw.get("instruments"), dict) else {}
    tracked_cards = {str(row.get("card") or "").strip().upper() for row in card_rows}
    tracked_cards.add("UOB ONE CARD")
    override_rate = raw.get("sgdPerCny")
    if override_rate is not None:
        if isinstance(override_rate, bool) or not isinstance(override_rate, (int, float)) \
                or not math.isfinite(override_rate) or override_rate <= 0:
            raise SystemExit("manual/wechat_payments.json sgdPerCny must be a positive number")

    # The tracker's own CNY charges, as (date, rate) for the estimate.
    cny_rows = []
    for row in card_rows:
        foreign = str(row.get("foreign") or "")
        if foreign.upper().startswith("CNY"):
            try:
                cny = float(foreign[3:].replace(",", "").strip())
                if cny > 0 and row.get("amount"):
                    cny_rows.append((str(row.get("date")), round(float(row["amount"]) / cny, 4), cny))
            except (TypeError, ValueError):
                continue

    def nearest_rate(date_value):
        if override_rate:
            return float(override_rate), "manual sgdPerCny"
        if not cny_rows:
            return None, ""
        target = datetime.strptime(date_value, "%Y-%m-%d")
        best = min(cny_rows, key=lambda item: abs((datetime.strptime(item[0], "%Y-%m-%d") - target).days))
        return best[1], "this tracker's CNY charge on %s" % best[0]

    def order_like(text):
        return bool(re.search(r"\d{8,}", text)) or not text

    charges = []
    evidence = {}
    used_rows = set()
    unmapped = set()
    seen = set()
    cny_total = 0.0
    sgd_total = 0.0
    for index, payment in enumerate(raw["payments"], 1):
        label = "WeChat payment %d" % index
        if not isinstance(payment, dict):
            raise SystemExit("%s must be an object" % label)
        pay_id = str(payment.get("id") or "")
        if not pay_id.startswith("wx_") or pay_id in seen:
            raise SystemExit("%s needs a unique id starting with wx_" % label)
        seen.add(pay_id)
        try:
            datetime.strptime(str(payment.get("date")), "%Y-%m-%d")
        except (TypeError, ValueError):
            raise SystemExit("%s has an invalid date %r" % (label, payment.get("date")))
        amount_cny = payment.get("amountCny")
        if isinstance(amount_cny, bool) or not isinstance(amount_cny, (int, float)) \
                or not math.isfinite(amount_cny) or amount_cny < 0:
            raise SystemExit("%s has an invalid amountCny %r" % (label, amount_cny))
        direction = str(payment.get("direction") or "")
        if direction not in ("expense", "income", "neutral"):
            raise SystemExit("%s has an invalid direction %r" % (label, direction))
        for field in ("method", "status", "counterparty", "product", "type", "time", "remark"):
            if payment.get(field) is not None and not isinstance(payment.get(field), str):
                raise SystemExit("%s %s must be text" % (label, field))
        if direction == "neutral" or str(payment.get("status") or "") not in WALLET_STATUS_OK or amount_cny <= 0:
            continue
        method = str(payment.get("method") or "").strip()
        instrument = str(instruments.get(method) or "").strip()
        if not instrument:
            # Until the method is mapped with --instrument nobody knows
            # whether this card is one of the statements or another wallet.
            unmapped.add(method or "unknown")
            continue
        counterparty = str(payment.get("counterparty") or "").strip()
        product = str(payment.get("product") or "").strip()
        name = counterparty if order_like(product) else product
        if instrument.upper() in tracked_cards:
            # Evidence for a statement row: same CNY amount within the window.
            candidates = [
                row for row in card_rows
                if row.get("id") not in used_rows
                and str(row.get("foreign") or "").upper().startswith("CNY")
                and abs(float(str(row.get("foreign"))[3:].replace(",", "").strip() or 0) - amount_cny) < 0.005
                and abs((datetime.strptime(str(row.get("date")), "%Y-%m-%d")
                         - datetime.strptime(str(payment.get("date")), "%Y-%m-%d")).days)
                <= WECHAT_EVIDENCE_WINDOW_DAYS
            ]
            if len(candidates) == 1:
                used_rows.add(candidates[0]["id"])
                evidence[candidates[0]["id"]] = {
                    "id": pay_id, "counterparty": counterparty, "product": product,
                    "name": name, "amountCny": round(float(amount_cny), 2),
                    "method": method, "time": str(payment.get("time") or ""),
                }
            continue
        rate, rate_source = nearest_rate(str(payment.get("date")))
        if rate is None:
            raise SystemExit("%s cannot be estimated: no CNY charge in the statements and no sgdPerCny" % label)
        sgd = round(float(amount_cny) * rate, 2)
        cny_total += float(amount_cny)
        sgd_total += sgd
        charges.append({
            "id": pay_id,
            "date": str(payment.get("date")),
            "month": str(payment.get("date"))[:7],
            "description": counterparty or name,
            "displayName": name,
            "amount": sgd,
            "amountCny": round(float(amount_cny), 2),
            "foreign": "CNY %.2f" % float(amount_cny),
            "estimated": True,
            "rate": rate,
            "rateSource": rate_source,
            "type": "debit" if direction == "expense" else "refund",
            "category": "Travel",
            "paidBy": instrument,
            "method": method,
            "status": str(payment.get("status") or ""),
            "time": str(payment.get("time") or ""),
            "transactionNo": str(payment.get("transactionNo") or ""),
            "ownerTag": "",
            "reason": "wechat",
        })
    charges.sort(key=lambda record: (record["date"], record["id"]))
    stats = {
        "payments": len(raw["payments"]),
        "wallet": len(charges),
        "evidence": len(evidence),
        "unmappedMethods": sorted(unmapped),
        "cnyTotal": round(cny_total, 2),
        "sgdEstimate": round(sgd_total, 2),
        "rate": float(override_rate) if override_rate else (cny_rows and round(
            sorted(item[1] for item in cny_rows)[len(cny_rows) // 2], 4) or None),
    }
    return charges, evidence, stats


def attach_klook(record, match, name_allowed):
    """Put a Klook link on a published row. The order name is derived
    evidence like a Trip.com product name, so it carries its own source."""
    orders = match["orders"]
    if name_allowed:
        names = list(dict.fromkeys(order["name"] for order in orders))
        record["displayName"] = names[0] if len(names) == 1 else "%d Klook orders" % len(orders)
        record["displayNameSource"] = "klook-order"
    record["klookOrder"] = orders[0]
    if len(orders) > 1:
        record["klookOrders"] = orders
    record["klookMatch"] = {"kind": match["kind"], "note": match["note"]}


def prepare_shopee_orders(order_data, card_rows):
    """Validate Shopee orders and link exact or explicitly bundled charges.

    Automatic links need an amount group with equal order/statement
    cardinality inside the statement window, and only orders at or below
    ``statementOrderMaxHistoryIndex`` take part. ``statementAggregates`` adds
    reviewed bundles: one statement charge that paid for two or more orders
    whose totals add to it exactly. Those orders share the charge's date and
    transaction ID, and the dashboard shows them one row per order while
    keeping the combined charge as evidence.
    """
    raw_orders = order_data.get("orders", []) if isinstance(order_data, dict) else []
    if not isinstance(raw_orders, list):
        raise SystemExit("manual/shopee_orders.json orders must be a list")
    start = order_data.get("statementFrom")
    through = order_data.get("statementThrough")
    match_history_limit = order_data.get("statementOrderMaxHistoryIndex")
    match_statements = order_data.get("matchStatements", True)
    if not isinstance(match_statements, bool):
        raise SystemExit("Shopee matchStatements must be a boolean")
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
        pet_text = " ".join([merchant] + items).upper()
        if any(token in pet_text for token in (
                "SMARTPAW", "MIUMIU PETSHOP", "MIUMIUPETSHOP", "PET TECH", "DAILY DELIGHT",
                "TRIADCATS", "NEXGARD", "CHURU", "S & S PET", "PAWSYMART",
                "WEPETS", "DOGCAT", "FISHNPETZ", "GESH CATS", "YAPPY PETS",
                "PAW LOBBY", "DABUPET", "CATISLAND", "4FURS", "PET WISHES",
                "FUNNY_PET", "ZATE PET", "PAW LOBBY", "PET WISHES")):
            category = "Pet care"
        elif merchant.strip().lower() == "shopee supermarket":
            category = "Groceries"
        else:
            category = "Shopping"
        orders.append({
            "orderId": order_id,
            "merchant": merchant.strip(),
            "status": status,
            "amount": amount,
            "items": [item.strip() for item in items],
            "historyIndex": history_index,
            "category": category,
        })

    eligible_rows = {}
    for row in card_rows:
        row_date = row.get("date")
        if (row.get("credit") or not is_shopee_description(row.get("description", ""))
                or not row_date or not start or row_date < start or row_date > through):
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            raise SystemExit("Shopee statement row has no stable transaction ID")
        if row_id in eligible_rows:
            raise SystemExit("Shopee statement rows repeat a stable transaction ID")
        eligible_rows[row_id] = row

    by_transaction = {}
    reserved_orders = set()
    reserved_rows = set()
    aggregate_matches = order_data.get("statementAggregates", [])
    if not isinstance(aggregate_matches, list):
        raise SystemExit("Shopee statementAggregates must be a list")
    orders_by_id = {order["orderId"]: order for order in orders}
    for index, raw in enumerate(aggregate_matches, 1):
        label = "Shopee statement aggregate %d" % index
        if not isinstance(raw, dict):
            raise SystemExit("%s must be an object" % label)
        transaction_id = raw.get("transactionId")
        order_ids = raw.get("orderIds")
        note = raw.get("note")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise SystemExit("%s has an invalid transactionId" % label)
        if (not isinstance(order_ids, list) or len(order_ids) < 2
                or any(not isinstance(value, str) or not value for value in order_ids)
                or len(set(order_ids)) != len(order_ids)):
            raise SystemExit("%s has invalid orderIds" % label)
        if not isinstance(note, str) or not note.strip():
            raise SystemExit("%s must explain the reconciliation evidence" % label)
        row = eligible_rows.get(transaction_id)
        if row is None:
            raise SystemExit("%s names an unknown Shopee transaction" % label)
        if transaction_id in reserved_rows:
            raise SystemExit("%s repeats a Shopee transaction" % label)
        linked_orders = []
        for order_id in order_ids:
            order = orders_by_id.get(order_id)
            if order is None:
                raise SystemExit("%s names an unknown Shopee order" % label)
            if order_id in reserved_orders:
                raise SystemExit("%s repeats a Shopee order" % label)
            if (match_history_limit is not None
                    and order["historyIndex"] > match_history_limit):
                raise SystemExit("%s names an order beyond the statement history cutoff" % label)
            linked_orders.append(order)
        order_cents = sum(int(round(order["amount"] * 100)) for order in linked_orders)
        row_cents = int(round(float(row.get("amount", 0)) * 100))
        if order_cents != row_cents:
            raise SystemExit("%s order totals do not equal the statement charge" % label)
        linked_orders.sort(key=lambda item: item["historyIndex"])
        for order in linked_orders:
            order["statementTransactionId"] = transaction_id
            order["date"] = row["date"]
            reserved_orders.add(order["orderId"])
        reserved_rows.add(transaction_id)
        by_transaction[transaction_id] = {
            "orders": linked_orders,
            "kind": "aggregate",
            "note": note.strip(),
        }

    order_groups = {}
    for order in orders:
        if order["orderId"] in reserved_orders:
            continue
        if (match_history_limit is not None
                and order["historyIndex"] > match_history_limit):
            continue
        if match_statements:
            order_groups.setdefault(int(round(order["amount"] * 100)), []).append(order)
    card_groups = {}
    for row_id, row in eligible_rows.items():
        if row_id in reserved_rows:
            continue
        card_groups.setdefault(int(round(float(row.get("amount", 0)) * 100)), []).append(row)

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


# Trip.com's export writes dates as "June 12, 2025"; the importer keeps that
# text verbatim, so the builder parses it here for the match window.
TRIP_BOOKING_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d")
# A prepaid Trip.com booking is charged when it is made. The card posts the
# same day or the next; a week absorbs weekends and late captures without
# letting a booking from another trip claim a same-priced charge years away.
TRIP_MATCH_WINDOW_DAYS = 7
TRIP_BOOKING_NO_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9-]{4,39}")
TRIP_CANCELLED_STATUS = "cancelled"
TRIP_RECONCILIATION_KINDS = {
    "aggregate",
    "discounted",
    "price-adjustment",
    "refund",
    "split-payment",
    "status-resolved",
}


def parse_trip_date(value):
    text = str(value or "").strip()
    for pattern in TRIP_BOOKING_DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def normalize_trip_booking_no(value):
    """Booking numbers are digit strings; a spreadsheet may hand them over as numbers."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if math.isfinite(value) and value.is_integer() else ""
    return str(value or "").strip().upper()


def prepare_trip_bookings(booking_data, card_rows, reconciliation_data=None):
    """Validate Trip.com rows and link exact plus explicitly reconciled activity.

    A charge is named after a booking only when the two agree to the cent in
    SGD, fall within TRIP_MATCH_WINDOW_DAYS of each other, and neither has any
    other candidate. ``reconciliation_data`` can add reviewed links for cases
    the statement cannot express one-to-one: discounts, replacement-booking
    adjustments, split payments, aggregate charges, and refunds. These links
    name exact stable transaction IDs and booking numbers; no amount tolerance
    or positional guess is applied at build time.

    Returns (bookings, by_transaction, stats).
    """
    if booking_data is None:
        booking_data = {"bookings": []}
    if not isinstance(booking_data, dict) or not isinstance(booking_data.get("bookings", []), list):
        raise SystemExit("manual/trip_bookings.json must be an object with a bookings list")
    raw_bookings = booking_data.get("bookings", [])
    bookings = []
    seen = set()
    for index, raw in enumerate(raw_bookings, 1):
        label = "Trip.com booking %d" % index
        if not isinstance(raw, dict):
            raise SystemExit("%s must be an object" % label)
        booking_no = normalize_trip_booking_no(raw.get("bookingNo"))
        product_name = str(raw.get("productName") or "").strip()
        currency = str(raw.get("currency") or "").strip().upper()
        if not TRIP_BOOKING_NO_PATTERN.fullmatch(booking_no):
            raise SystemExit("%s has a missing or malformed booking number" % label)
        if booking_no in seen:
            # Index only: booking numbers stay out of the console and logs.
            raise SystemExit("%s repeats an earlier booking number" % label)
        if not product_name:
            raise SystemExit("%s has no product name" % label)
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise SystemExit("%s has an invalid currency" % label)
        amount = raw.get("amount")
        if amount is not None:
            if isinstance(amount, bool):
                raise SystemExit("%s has an invalid amount" % label)
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                raise SystemExit("%s has an invalid amount" % label)
            if not math.isfinite(amount) or amount < 0:
                raise SystemExit("%s has an invalid amount" % label)
            amount = round(amount, 2)
        for field in ("status", "productType", "bookingDate", "travelTime", "traveller", "sourceFile"):
            if raw.get(field) is not None and not isinstance(raw.get(field), str):
                raise SystemExit("%s %s must be text" % (label, field))
        seen.add(booking_no)
        bookings.append({
            "bookingNo": booking_no,
            "status": str(raw.get("status") or "").strip(),
            "productType": str(raw.get("productType") or "").strip(),
            "bookingDate": str(raw.get("bookingDate") or "").strip(),
            "productName": product_name,
            "travelTime": str(raw.get("travelTime") or "").strip(),
            "traveller": str(raw.get("traveller") or "").strip(),
            "currency": currency,
            "amount": amount,
            "sourceFile": str(raw.get("sourceFile") or "").strip(),
        })

    stats = {
        "bookings": len(bookings),
        "cancelled": sum(1 for b in bookings if b["status"].lower() == TRIP_CANCELLED_STATUS),
        "foreignCurrency": sum(1 for b in bookings if b["currency"] != "SGD"),
        "zeroAmount": sum(1 for b in bookings if not b["amount"]),
        "undated": 0,
        "statementCharges": 0,
        "statementRefunds": 0,
        "matched": 0,
        "matchedRefunds": 0,
        "matchedTransactions": 0,
        "matchedBookings": 0,
        "matchedCancelled": 0,
        "ambiguousCharges": 0,
        "unmatchedCharges": 0,
        "unmatchedRefunds": 0,
        "unmatchedBookings": 0,
    }

    # Candidates in both directions; a link needs exactly one on each side.
    booking_candidates = {}
    dated = []
    for booking in bookings:
        if booking["currency"] != "SGD" or not booking["amount"]:
            continue
        booking_day = parse_trip_date(booking["bookingDate"])
        if booking_day is None:
            stats["undated"] += 1
            continue
        dated.append((booking, booking_day, int(round(booking["amount"] * 100))))
        booking_candidates[booking["bookingNo"]] = []
    charges = []
    trip_rows_by_id = {}
    for row in card_rows:
        if not is_trip_description(row.get("description", "")):
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            raise SystemExit("Trip.com statement row has no stable transaction ID")
        if row_id in trip_rows_by_id:
            raise SystemExit("Trip.com statement rows repeat a stable transaction ID")
        trip_rows_by_id[row_id] = row
        if row.get("credit"):
            stats["statementRefunds"] += 1
            continue
        stats["statementCharges"] += 1
        row_day = parse_trip_date(row.get("date"))
        amount = round(float(row.get("amount", 0)), 2)
        if row_day is None or amount <= 0:
            continue
        charges.append((row, row_day, int(round(amount * 100))))
    if reconciliation_data is None:
        reconciliation_data = {"links": []}
    if (not isinstance(reconciliation_data, dict)
            or not isinstance(reconciliation_data.get("links", []), list)):
        raise SystemExit(
            "manual/trip_booking_reconciliation.json must be an object with a links list"
        )
    bookings_by_no = {booking["bookingNo"]: booking for booking in bookings}
    by_transaction = {}
    manually_linked_rows = set()
    for index, raw in enumerate(reconciliation_data.get("links", []), 1):
        label = "Trip.com reconciliation %d" % index
        if not isinstance(raw, dict):
            raise SystemExit("%s must be an object" % label)
        transaction_ids = raw.get("transactionIds")
        booking_nos = raw.get("bookingNos")
        kind = str(raw.get("kind") or "").strip()
        note = raw.get("note", "")
        if (not isinstance(transaction_ids, list) or not transaction_ids
                or any(not isinstance(value, str) or not value for value in transaction_ids)
                or len(set(transaction_ids)) != len(transaction_ids)):
            raise SystemExit("%s has invalid transactionIds" % label)
        if not isinstance(booking_nos, list) or not booking_nos:
            raise SystemExit("%s has invalid bookingNos" % label)
        normalized_nos = [normalize_trip_booking_no(value) for value in booking_nos]
        if (any(not value for value in normalized_nos)
                or len(set(normalized_nos)) != len(normalized_nos)):
            raise SystemExit("%s has invalid bookingNos" % label)
        if kind not in TRIP_RECONCILIATION_KINDS:
            raise SystemExit("%s has an invalid kind" % label)
        if not isinstance(note, str) or not note.strip():
            raise SystemExit("%s must explain the reconciliation evidence" % label)
        linked_bookings = []
        for booking_no in normalized_nos:
            booking = bookings_by_no.get(booking_no)
            if booking is None:
                raise SystemExit("%s names an unknown booking" % label)
            if booking["currency"] != "SGD" or not booking["amount"]:
                raise SystemExit("%s names a booking without a usable SGD amount" % label)
            linked_bookings.append(booking)
        match = {
            "bookings": linked_bookings,
            "kind": kind,
            "note": note.strip(),
        }
        for transaction_id in transaction_ids:
            if transaction_id not in trip_rows_by_id:
                raise SystemExit("%s names an unknown Trip.com transaction" % label)
            if transaction_id in manually_linked_rows:
                raise SystemExit("%s repeats a transaction from an earlier reconciliation" % label)
            manually_linked_rows.add(transaction_id)
            by_transaction[transaction_id] = match

    auto_charges = [entry for entry in charges if entry[0]["id"] not in manually_linked_rows]
    row_candidates = {row["id"]: [] for row, _, _ in auto_charges}
    auto_booking_candidates = {booking_no: [] for booking_no in booking_candidates}
    for booking, booking_day, booking_cents in dated:
        for row, row_day, row_cents in auto_charges:
            if row_cents != booking_cents:
                continue
            if abs((row_day - booking_day).days) > TRIP_MATCH_WINDOW_DAYS:
                continue
            auto_booking_candidates[booking["bookingNo"]].append(row["id"])
            row_candidates[row["id"]].append(booking)

    for row, _, _ in auto_charges:
        candidates = row_candidates[row["id"]]
        if len(candidates) > 1:
            stats["ambiguousCharges"] += 1
            continue
        if (len(candidates) == 1
                and len(auto_booking_candidates[candidates[0]["bookingNo"]]) == 1):
            by_transaction[row["id"]] = candidates[0]
        elif len(candidates) == 1:
            stats["ambiguousCharges"] += 1

    def matched_bookings(match):
        return match["bookings"] if "bookings" in match else [match]

    linked = {
        booking["bookingNo"]
        for match in by_transaction.values()
        for booking in matched_bookings(match)
    }
    matched_charge_ids = {
        transaction_id for transaction_id in by_transaction
        if not trip_rows_by_id[transaction_id].get("credit")
    }
    matched_refund_ids = set(by_transaction) - matched_charge_ids
    stats["matched"] = len(matched_charge_ids)
    stats["matchedRefunds"] = len(matched_refund_ids)
    stats["matchedTransactions"] = len(by_transaction)
    stats["matchedBookings"] = len(linked)
    stats["matchedCancelled"] = sum(
        1 for transaction_id in matched_charge_ids
        if any(booking["status"].lower() == TRIP_CANCELLED_STATUS
               for booking in matched_bookings(by_transaction[transaction_id]))
    )
    stats["unmatchedCharges"] = stats["statementCharges"] - stats["matched"]
    stats["unmatchedRefunds"] = stats["statementRefunds"] - stats["matchedRefunds"]
    stats["unmatchedBookings"] = sum(1 for booking, _, _ in dated if booking["bookingNo"] not in linked)
    return bookings, by_transaction, stats


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
    trip_bookings, trip_by_transaction, trip_stats = prepare_trip_bookings(
        manual("trip_bookings.json", {"bookings": []}),
        rows,
        manual("trip_booking_reconciliation.json", {"links": []}),
    )
    partner_travel = prepare_partner_travel(manual("partner_travel.json", {}))
    wechat_raw = manual("wechat_payments.json", {})
    wallet_charges, wechat_by_transaction, wallet_stats = prepare_wechat_payments(
        wechat_raw, rows)
    wallet_travel = {
        "source": (wechat_raw.get("source") if isinstance(wechat_raw, dict) else None) or {},
        "instruments": (wechat_raw.get("instruments") if isinstance(wechat_raw, dict) else None) or {},
        "charges": wallet_charges,
    }
    # Klook orders belong to this account, so a charge on the other person's
    # card can be one of them too; both sets are linked in one pass so an
    # amount is never claimed twice.
    klook_orders, klook_by_transaction, klook_stats = prepare_klook_orders(
        manual("klook_orders.json", {"orders": []}), rows, partner_travel["charges"],
        min((r["month"] for r in rows), default=None))
    for charge in partner_travel["charges"]:
        if charge["id"] in klook_by_transaction:
            attach_klook(charge, klook_by_transaction[charge["id"]], not charge.get("displayName"))
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
        shopee_match = shopee_by_transaction.get(r["id"])
        if shopee_match and "orders" in shopee_match:
            shopee_row_orders = shopee_match["orders"]
            shopee_match_kind = shopee_match["kind"]
            shopee_match_note = shopee_match["note"]
        elif shopee_match:
            shopee_row_orders = [shopee_match]
            shopee_match_kind = "exact"
            shopee_match_note = SHOPEE_EXACT_MATCH_NOTE
        else:
            shopee_row_orders = []
            shopee_match_kind = None
            shopee_match_note = None
        shopee_order = shopee_row_orders[0] if shopee_row_orders else None
        trip_match = trip_by_transaction.get(r["id"])
        if trip_match and "bookings" in trip_match:
            trip_row_bookings = trip_match["bookings"]
            trip_match_kind = trip_match["kind"]
            trip_match_note = trip_match["note"]
        elif trip_match:
            trip_row_bookings = [trip_match]
            trip_match_kind = "exact"
            trip_match_note = (
                "The booking date and SGD total uniquely match this statement charge."
            )
        else:
            trip_row_bookings = []
            trip_match_kind = None
            trip_match_note = None
        trip_booking = trip_row_bookings[0] if trip_row_bookings else None
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
            else "trip-booking" if trip_booking
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
        # Yx's clone is a single-owner ledger; legacy/shared owner tags do not
        # apply and imported charges should never remain unassigned.
        owner = PROFILE_OWNER
        owner_source = "profile-default"
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
        if trip_booking:
            # The product name is derived evidence, not a saved edit. The
            # source marker lets the dashboard keep it out of the override
            # editor and out of grouped labels, and lets the server refuse to
            # freeze it as an override on an unrelated save.
            product_names = list(dict.fromkeys(
                booking["productName"] for booking in trip_row_bookings
            ))
            record["displayName"] = (
                product_names[0] if len(product_names) == 1
                else "%d Trip.com bookings" % len(trip_row_bookings)
            )
            record["displayNameSource"] = "trip-booking"
            booking_details = [
                {
                    key: booking[key]
                    for key in (
                    "bookingNo", "status", "productType", "bookingDate", "productName",
                    "travelTime", "traveller", "currency", "amount", "sourceFile"
                    )
                }
                for booking in trip_row_bookings
            ]
            record["tripBooking"] = booking_details[0]
            if len(booking_details) > 1:
                record["tripBookings"] = booking_details
            record["tripMatch"] = {
                "kind": trip_match_kind,
                "note": trip_match_note,
            }
        if r["id"] in klook_by_transaction:
            attach_klook(record, klook_by_transaction[r["id"]], not trip_booking)
        if r["id"] in wechat_by_transaction:
            # A WeChat payment on this card names the merchant behind a bare
            # CNY charge; derived evidence, so it carries its own source.
            wechat = wechat_by_transaction[r["id"]]
            record["wechat"] = wechat
            if not record.get("displayName") and wechat.get("name"):
                record["displayName"] = wechat["name"]
                record["displayNameSource"] = "wechat-payment"
        # A hand-set destination for a travel charge whose descriptor names
        # only the platform's billing entity; the dashboard reads it before
        # its own inference.
        if override.get("destination"):
            record["destination"] = override["destination"]
        if is_trip_description(r["description"]):
            # Every Trip.com statement row, matched or not, charge or refund,
            # carries the marker so the "Trip.com only" filter and the
            # matcher agree on what counts as Trip.com by construction.
            record["trip"] = {"status": "booking-matched" if trip_booking else "unmatched"}
        if r.get("foreign"):
            record["foreign"] = r["foreign"]
        if foodpanda_order:
            record["foodpanda"] = {
                key: foodpanda_order[key]
                for key in ("orderId", "date", "time", "fulfillment", "merchant", "amount")
            }
        if shopee_order:
            # A reviewed bundle publishes every order under shopeeOrders and
            # keeps the first as the primary detail; the match evidence says
            # whether the link was an exact amount or a reviewed aggregate.
            shopee_details = [
                {
                    key: order[key]
                    for key in (
                        "orderId", "merchant", "status", "amount", "items", "historyIndex",
                        "category"
                    )
                }
                for order in shopee_row_orders
            ]
            record["shopee"] = shopee_details[0]
            if len(shopee_details) > 1:
                record["shopeeOrders"] = shopee_details
            record["shopeeMatch"] = {
                "kind": shopee_match_kind,
                "note": shopee_match_note,
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
            record["displayNameSource"] = "override"
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
            "matched": sum(
                1 for order in shopee_orders if order.get("statementTransactionId")
            ),
            "unmatched": sum(
                1 for order in shopee_orders if not order.get("statementTransactionId")
            ),
            "groceries": sum(
                1 for order in shopee_orders if order["category"] == "Groceries"
            ),
        },
        "trip": trip_stats,
        "klook": klook_stats,
        "wallet": wallet_stats,
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
                # Trip.com bookings are published only on the charges they
                # explain; the full export (traveller names included) has no
                # reader in the dashboard and stays in manual/.
                "grabReceipts": grab_receipts,
                "insurance": insurance,
                # Charges the other tracker paid for shared travel; read by
                # the Travel tab, never by the ledger.
                "partnerTravel": partner_travel,
                # Spending on cards the statements never show (WeChat Pay on
                # YouTrip and the like), SGD estimated; read by the Travel tab.
                "walletTravel": wallet_travel,
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
    print("Klook orders %d (%d expired): %d charges and %d refunds linked, %d paid orders awaiting a statement"
          % (klook_stats["orders"], klook_stats["expired"], klook_stats["matchedCharges"],
             klook_stats["matchedRefunds"], len(klook_stats["awaiting"])))
    print("WeChat payments %d: %d on other cards (CNY %.2f, about S$%.2f), %d as evidence for statement rows%s"
          % (wallet_stats["payments"], wallet_stats["wallet"], wallet_stats["cnyTotal"],
             wallet_stats["sgdEstimate"], wallet_stats["evidence"],
             "; unmapped methods " + ", ".join(wallet_stats["unmappedMethods"])
             if wallet_stats["unmappedMethods"] else ""))
    print("Partner travel charges %d paid by %s"
          % (len(partner_travel["charges"]), partner_travel["paidBy"] or "nobody"))
    print("Salary steps %d, annual rows %d, game sales %d"
          % (len(salary.get("steps", [])), len(salary.get("years", [])), len(sales)))
    print("Remarks %d" % len(remarks_by_id))
    print("Transaction overrides %d" % len(transaction_overrides))
    print("Foodpanda orders %d (%d matched to statement rows)"
          % (len(foodpanda_orders), len(foodpanda_by_transaction)))
    shopee_matched_orders = sum(
        1 for order in shopee_orders if order.get("statementTransactionId")
    )
    print("Shopee orders %d (%d matched across %d statement rows)"
          % (len(shopee_orders), shopee_matched_orders, len(shopee_by_transaction)))
    print("Trip.com bookings %d (%d/%d charges and %d/%d refunds matched across "
          "%d bookings; %d charges ambiguous; %d foreign-currency bookings)"
          % (trip_stats["bookings"], trip_stats["matched"], trip_stats["statementCharges"],
             trip_stats["matchedRefunds"], trip_stats["statementRefunds"],
             trip_stats["matchedBookings"], trip_stats["ambiguousCharges"],
             trip_stats["foreignCurrency"]))
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
