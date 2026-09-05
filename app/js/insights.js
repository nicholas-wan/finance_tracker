// Rule-based insight engine. Every finding is derived arithmetic over the
// generated data — no heuristics that can't be traced back to a number.
window.Insights = (function () {
  "use strict";

  var EXCLUDED = { Payment: true, Rebates: true };
  var MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function money(n) {
    var abs = Math.abs(n);
    var digits = abs >= 1000 ? 0 : 2;
    return "S$" + abs.toLocaleString("en-SG", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function settlementMoney(n) {
    return "S$" + Math.abs(n).toLocaleString("en-SG", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }
  function label(month) {
    var p = month.split("-");
    return MONTH_NAMES[parseInt(p[1], 10) - 1] + " " + p[0];
  }
  function signed(t) { return t.type === "debit" ? t.amount : -t.amount; }
  var TRAVEL_COUNTRY_RULES = [
    ["Hong Kong", /HONG KONG|\bHKG\b/i],
    ["Taiwan", /TAIWAN|TAIPEI|\bTPE\b|\bKHH\b/i],
    ["Macau", /MACAU|MACAO|\bMFM\b/i],
    ["China", /\b(?:PVG|CAN|TFU)\b|SHANGHAI|BEIJING|SHENZHEN|GUANGZHOU|CHENGDU|ZHANGJIAJIE|HAIKOU|HANGZHOU|WUXI|ZHONGSHAN|CHIMELONG|TOY STORY HOTEL/i],
    ["South Korea", /YEOUIDO|SEOUL|SOUTH KOREA|\b(?:ICN|GMP)\b/i],
    ["Japan", /SARDONYX|\bUENO\b|MIYAJIMA|NARITA|SUNSHINE AQUARIUM|TOKYO|JAPAN|\b(?:NRT|HND|KIX|HIJ)\b/i],
    ["Canada", /NIAGARA|TORONTO|ONTARIO|RIPLEYSCANA|CANADA|\bYYZ\b/i],
    ["United States", /BOSTON|BUFFALO|WILMINGTON|UNITED STATES|\bUSA\b|\b(?:BOS|BUF)\b/i],
    ["Australia", /AUSTRALIANETA|SYDNEY|TARONGA|AUSTRALIA|\bSYD\b/i],
    ["France", /TOUR EIFFEL|TROCADERO|PARIS|FRANCE|\bCDG\b/i],
    ["Germany", /BERLIN|GERMANY|\bBER\b/i],
    ["United Kingdom", /UKVI|UNITED KINGDOM|WWW\.GOV\.UK|\bLONDON\b|\bLHR\b/i],
    ["Malaysia", /MALAYSIA|KUALA LUMPUR|JOHOR|PETALING JAYA|PENANG|\b(?:KUL|JHB|PEN)\b/i],
    ["Thailand", /THAILAND|BANGKOK|PHUKET|CHIANG MAI|\b(?:BKK|DMK|HKT)\b/i],
    ["Vietnam", /VIETNAM|HANOI|HO CHI MINH|SAIGON|DA NANG|\b(?:SGN|HAN|DAD)\b/i],
    ["Indonesia", /INDONESIA|\bBALI\b|JAKARTA|DENPASAR|\b(?:DPS|CGK)\b/i],
    ["Singapore", /RITZ CARLTON MILLENIA|MARINA BAY SANDS|SENTOSA/i]
  ];
  // A foreign-currency charge names its country almost as well as an
  // airport code does, for currencies used by one country. USD and EUR are
  // left out because they are not.
  var CURRENCY_COUNTRIES = {
    CNY: "China", HKD: "Hong Kong", MOP: "Macau", TWD: "Taiwan", JPY: "Japan",
    KRW: "South Korea", MYR: "Malaysia", THB: "Thailand", VND: "Vietnam",
    IDR: "Indonesia", PHP: "Philippines", AUD: "Australia", NZD: "New Zealand",
    GBP: "United Kingdom", CAD: "Canada", CHF: "Switzerland", INR: "India"
  };
  // The city inside a destination, from the same evidence, for a trip label
  // that reads "Chengdu, China" rather than a bare country.
  var TRAVEL_CITY_RULES = [
    ["Hong Kong", /HONG KONG|\bHKG\b/i],
    ["Macau", /MACAU|MACAO|\bMFM\b/i],
    ["Taipei", /TAIPEI|\bTPE\b/i],
    ["Kaohsiung", /KAOHSIUNG|\bKHH\b/i],
    ["Shanghai", /SHANGHAI|\bPVG\b|\bSHA\b|TOY STORY HOTEL/i],
    ["Beijing", /BEIJING|\b(?:PEK|PKX)\b/i],
    ["Shenzhen", /SHENZHEN|\bSZX\b/i],
    ["Guangzhou", /GUANGZHOU|\bCAN\b|CHIMELONG/i],
    ["Chengdu", /CHENGDU|\bTFU\b|\bCTU\b/i],
    ["Zhangjiajie", /ZHANGJIAJIE/i],
    ["Haikou", /HAIKOU/i],
    ["Hangzhou", /HANGZHOU/i],
    ["Wuxi", /WUXI/i],
    ["Zhongshan", /ZHONGSHAN/i],
    ["Seoul", /SEOUL|YEOUIDO|\b(?:ICN|GMP)\b/i],
    ["Tokyo", /TOKYO|NARITA|\bUENO\b|SUNSHINE AQUARIUM|\b(?:NRT|HND)\b/i],
    ["Osaka", /OSAKA|\bKIX\b/i],
    ["Hiroshima", /HIROSHIMA|MIYAJIMA|\bHIJ\b|SARDONYX/i],
    ["Toronto", /TORONTO|ONTARIO|\bYYZ\b/i],
    ["Niagara Falls", /NIAGARA|RIPLEYSCANA/i],
    ["Boston", /BOSTON|\bBOS\b/i],
    ["Buffalo", /BUFFALO|\bBUF\b/i],
    ["Wilmington", /WILMINGTON/i],
    ["Sydney", /SYDNEY|TARONGA|\bSYD\b/i],
    ["Paris", /PARIS|TOUR EIFFEL|TROCADERO|\bCDG\b/i],
    ["Berlin", /BERLIN|\bBER\b/i],
    ["London", /\bLONDON\b|\bLHR\b/i],
    ["Kuala Lumpur", /KUALA LUMPUR|\bKUL\b/i],
    ["Johor Bahru", /JOHOR|\bJB\b/i],
    ["Petaling Jaya", /PETALING JAYA/i],
    ["Penang", /PENANG|\bPEN\b/i],
    ["Bangkok", /BANGKOK|\b(?:BKK|DMK)\b/i],
    ["Bali", /\bBALI\b|DENPASAR|\bDPS\b/i]
  ];
  function travelEvidence(transaction) {
    var bookings = Array.isArray(transaction.tripBookings) && transaction.tripBookings.length
      ? transaction.tripBookings : transaction.tripBooking ? [transaction.tripBooking] : [];
    return bookings.map(function (booking) {
      return [booking.productName, booking.productType].filter(Boolean).join(" ");
    }).concat([
      transaction.displayName,
      transaction.description
    ]).filter(Boolean).join(" ");
  }
  function currencyCountry(transaction) {
    var code = String(transaction.foreign || "").trim().slice(0, 3).toUpperCase();
    return CURRENCY_COUNTRIES[code] || "";
  }
  // Destination evidence, strongest first: a saved destination, then the
  // booking or descriptor text, then a single-country currency.
  function travelCountry(transaction) {
    if (!transaction || transaction.category !== "Travel") return "";
    if (transaction.destination) return transaction.destination;
    var text = travelEvidence(transaction);
    for (var i = 0; i < TRAVEL_COUNTRY_RULES.length; i += 1) {
      if (TRAVEL_COUNTRY_RULES[i][1].test(text)) return TRAVEL_COUNTRY_RULES[i][0];
    }
    return currencyCountry(transaction) || "Unknown";
  }
  // Every destination the rules or currencies can name, for the drawer's
  // destination picker.
  function destinations() {
    var names = {};
    TRAVEL_COUNTRY_RULES.forEach(function (rule) { names[rule[0]] = true; });
    Object.keys(CURRENCY_COUNTRIES).forEach(function (code) { names[CURRENCY_COUNTRIES[code]] = true; });
    return Object.keys(names).sort();
  }
  function travelCity(transaction) {
    if (!transaction || transaction.category !== "Travel") return "";
    var text = travelEvidence(transaction);
    for (var i = 0; i < TRAVEL_CITY_RULES.length; i += 1) {
      if (TRAVEL_CITY_RULES[i][1].test(text)) return TRAVEL_CITY_RULES[i][0];
    }
    return "";
  }
  // For a travel charge with no destination of its own: the known destination
  // of the nearest other travel charge within a week, since a platform
  // charge usually sits among the trip it paid for.
  function suggestedDestination(transaction, transactions) {
    if (!transaction || !transaction.date) return null;
    var best = null;
    (transactions || []).forEach(function (other) {
      if (!other || other.id === transaction.id || other.category !== "Travel" || !other.date) return;
      var country = travelCountry(other);
      if (!country || country === "Unknown") return;
      var gap = Math.abs(dayNumber(other.date) - dayNumber(transaction.date));
      if (gap > 7) return;
      if (!best || gap < best.gap || (gap === best.gap && Math.abs(other.amount) > Math.abs(best.amount))) {
        best = { country: country, gap: gap, date: other.date, amount: other.amount,
                 description: other.displayName || other.description };
      }
    });
    return best;
  }

  // ---------- Trips ----------
  //
  // A trip is the cluster of travel charges that belong to one journey. The
  // statement date is a poor anchor for that: flights are charged months
  // ahead and hotels weeks ahead. Trip.com bookings carry the travel date, so
  // a matched charge is anchored there and only bookingless rows fall back to
  // the statement date. Rows whose anchors sit within TRIP_GAP_DAYS of each
  // other form one trip; a lone known-country charge within TRIP_ATTACH_DAYS of
  // a trip to the same place (a visa, a ticket bought early) joins it, and any
  // foreign-currency charge dated inside a trip's window counts as spend on
  // the ground even when it was categorised as food or transport.
  var TRIP_GAP_DAYS = 5;
  var TRIP_ATTACH_DAYS = 60;
  var MONTH_INDEX = {
    january: 0, february: 1, march: 2, april: 3, may: 4, june: 5, july: 6,
    august: 7, september: 8, october: 9, november: 10, december: 11
  };
  var TRAVEL_DATE = /^(?:\d{1,2}:\d{2},?\s*)?([A-Za-z]+)\s+(\d{1,2})(?:,?\s*(\d{4}))?$/;

  function isoDate(year, month, day) {
    var date = new Date(Date.UTC(year, month, day));
    if (date.getUTCMonth() !== month || date.getUTCDate() !== day) return null;
    return date.toISOString().slice(0, 10);
  }
  function dayNumber(iso) {
    return Math.round(Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 86400000);
  }
  function daysApart(a, b) { return Math.abs(dayNumber(a) - dayNumber(b)); }

  // Trip.com exports travel dates as display text, and drops the year when
  // the travel falls in the booking year. "18:45, October 17, 2023" and
  // "May 10" both resolve; a date that would land before the booking is
  // taken to be in the following year.
  function parseTravelDate(text, bookingDate) {
    var match = TRAVEL_DATE.exec(String(text || "").trim());
    if (!match) return null;
    var month = MONTH_INDEX[match[1].toLowerCase()];
    if (month === undefined) return null;
    var day = parseInt(match[2], 10);
    if (match[3]) return isoDate(parseInt(match[3], 10), month, day);
    var booked = parseTravelDate(bookingDate, null);
    if (!booked) return null;
    var year = parseInt(booked.slice(0, 4), 10);
    var candidate = isoDate(year, month, day);
    if (!candidate) return null;
    return candidate < booked ? isoDate(year + 1, month, day) : candidate;
  }

  // The first and last travel date on a booking: one date for a hotel or a
  // ticket, two for a return flight.
  function travelWindow(booking) {
    if (!booking) return null;
    var dates = String(booking.travelTime || "").split(/\n+/).map(function (part) {
      return parseTravelDate(part, booking.bookingDate);
    }).filter(Boolean).sort();
    if (!dates.length) return null;
    return { start: dates[0], end: dates[dates.length - 1] };
  }

  function bookingsOf(transaction) {
    if (Array.isArray(transaction.tripBookings) && transaction.tripBookings.length) {
      return transaction.tripBookings;
    }
    return transaction.tripBooking ? [transaction.tripBooking] : [];
  }

  function tripSplitBucket(transaction) {
    var types = bookingsOf(transaction).map(function (booking) {
      return String(booking.productType || "");
    });
    if (types.indexOf("Flights") !== -1) return "Flights";
    if (types.indexOf("Hotels") !== -1) return "Hotels";
    if (types.length) return "Tickets & transfers";
    return "On the ground";
  }

  // A "confirmed" charge is money that actually left: a debit that no
  // matching refund reversed. Refund rows and reversed charges still belong
  // to a trip (their amounts net to zero inside it) but they are not counted
  // as charges, and a cancelled booking is not counted as a booking.
  function reversedIds(transactions) {
    var grouping = window.FinanceGrouping;
    if (!grouping || typeof grouping.reversedPairs !== "function") return {};
    return grouping.reversedPairs(transactions || []).hidden || {};
  }
  function isCancelledBooking(booking) {
    return String(booking && booking.status || "").toLowerCase() === "cancelled";
  }
  function isConfirmedCharge(transaction, hidden) {
    return transaction.type === "debit" && !hidden[transaction.id];
  }
  function hasConfirmedBooking(transaction) {
    return bookingsOf(transaction).some(function (booking) { return !isCancelledBooking(booking); });
  }
  function confirmedTravelCharges(transactions) {
    var hidden = reversedIds(transactions);
    return (transactions || []).filter(function (transaction) {
      return transaction.category === "Travel" && isConfirmedCharge(transaction, hidden);
    });
  }

  function isForeign(transaction) {
    var text = String(transaction.foreign || "").trim();
    return Boolean(text) && text.slice(0, 3).toUpperCase() !== "SGD";
  }

  function buildTrips(transactions) {
    var hidden = reversedIds(transactions);
    var anchored = [];
    var foreignOnly = [];
    (transactions || []).forEach(function (transaction) {
      if (!transaction || !transaction.date) return;
      if (transaction.category === "Travel") {
        var windows = bookingsOf(transaction).map(travelWindow).filter(Boolean);
        var start = transaction.date, end = transaction.date;
        if (windows.length) {
          start = windows.map(function (w) { return w.start; }).sort()[0];
          end = windows.map(function (w) { return w.end; }).sort().reverse()[0];
        }
        anchored.push({
          transaction: transaction, start: start, end: end,
          booked: windows.length > 0, country: travelCountry(transaction)
        });
      } else if (isForeign(transaction)) {
        foreignOnly.push(transaction);
      }
    });
    anchored.sort(function (a, b) {
      return a.start.localeCompare(b.start) || a.transaction.date.localeCompare(b.transaction.date);
    });

    var clusters = [];
    anchored.forEach(function (item) {
      var last = clusters[clusters.length - 1];
      // Items arrive sorted by start, so a row belongs to the open trip when
      // it starts before that trip's last day plus the gap.
      if (last && dayNumber(item.start) - dayNumber(last.end) <= TRIP_GAP_DAYS) {
        last.items.push(item);
        if (item.end > last.end) last.end = item.end;
      } else {
        clusters.push({ start: item.start, end: item.end, items: [item] });
      }
    });

    // Charges with no booking but a known destination join the nearest
    // booking-anchored trip to the same place: visas, tickets bought early,
    // a hotel paid on another platform.
    function knownCountry(cluster) {
      var totals = {};
      cluster.items.forEach(function (item) {
        if (item.country && item.country !== "Unknown") {
          totals[item.country] = (totals[item.country] || 0) + Math.abs(item.transaction.amount);
        }
      });
      var best = "";
      Object.keys(totals).forEach(function (country) {
        if (!best || totals[country] > totals[best]) best = country;
      });
      return best;
    }
    var merged = [];
    clusters.forEach(function (cluster) {
      cluster.primary = knownCountry(cluster);
      cluster.anchoredBy = cluster.items.some(function (item) { return item.booked; });
    });
    clusters.forEach(function (cluster, index) {
      if (!cluster.anchoredBy && cluster.primary) {
        var target = null, targetGap = Infinity;
        clusters.forEach(function (other, otherIndex) {
          if (otherIndex === index || other.merged || !other.anchoredBy) return;
          if (other.primary !== cluster.primary) return;
          var gap = Math.min(daysApart(cluster.start, other.end), daysApart(other.start, cluster.end));
          if (gap <= TRIP_ATTACH_DAYS && gap < targetGap) { target = other; targetGap = gap; }
        });
        if (target) {
          target.items = target.items.concat(cluster.items);
          cluster.merged = true;
          return;
        }
      }
      merged.push(cluster);
    });

    var trips = merged.map(function (cluster) {
      var ids = {};
      var rows = [];
      cluster.items.forEach(function (item) {
        if (!ids[item.transaction.id]) { ids[item.transaction.id] = true; rows.push(item.transaction); }
      });
      // The trip's dates come from its bookings when it has any, so a visa or
      // a ticket bought weeks ahead joins the trip without stretching it.
      var spanItems = cluster.anchoredBy
        ? cluster.items.filter(function (item) { return item.booked; })
        : cluster.items;
      var start = spanItems.map(function (item) { return item.start; }).sort()[0];
      var end = spanItems.map(function (item) { return item.end; }).sort().reverse()[0];
      // Foreign-currency spend dated inside the window is part of the trip.
      foreignOnly.forEach(function (transaction) {
        if (transaction.date >= start && transaction.date <= end && !ids[transaction.id]) {
          ids[transaction.id] = true;
          rows.push(transaction);
        }
      });
      var countries = {};
      var cities = {};
      var split = { "Flights": 0, "Hotels": 0, "Tickets & transfers": 0, "On the ground": 0 };
      var total = 0;
      rows.forEach(function (transaction) {
        var amount = signed(transaction);
        total += amount;
        split[tripSplitBucket(transaction)] += amount;
        var country = transaction.category === "Travel" ? travelCountry(transaction) : "";
        if (country) countries[country] = (countries[country] || 0) + Math.abs(amount);
        var city = travelCity(transaction);
        if (city) cities[city] = (cities[city] || 0) + Math.abs(amount);
      });
      var known = Object.keys(countries).filter(function (c) { return c !== "Unknown"; })
        .sort(function (a, b) { return countries[b] - countries[a]; });
      var cityList = Object.keys(cities).sort(function (a, b) { return cities[b] - cities[a]; });
      var days = daysApart(start, end) + 1;
      return {
        key: start + "|" + (known[0] || "Unknown"),
        start: start,
        end: end,
        days: days,
        countries: known,
        primary: known[0] || "Unknown",
        cities: cityList,
        city: cityList[0] || "",
        ids: rows.map(function (transaction) { return transaction.id; }),
        rowCount: rows.length,
        count: rows.filter(function (transaction) { return isConfirmedCharge(transaction, hidden); }).length,
        bookings: rows.filter(function (transaction) {
          return isConfirmedCharge(transaction, hidden) && hasConfirmedBooking(transaction);
        }).length,
        total: Math.round(total * 100) / 100,
        perDay: Math.round(total / days * 100) / 100,
        split: split,
        anchored: cluster.anchoredBy
      };
    });
    trips.sort(function (a, b) { return b.start.localeCompare(a.start); });
    return trips;
  }

  function salaryStreamKey(description) {
    return String(description || "")
      .toUpperCase()
      .replace(/[0-9]+/g, " ")
      .replace(/[^A-Z]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }
  function incomeForecast(transactions, statementMonths) {
    var salaryRows = (transactions || []).filter(function (transaction) {
      return transaction.direction === "deposit" && transaction.flow === "Salary";
    });
    var totals = {};
    salaryRows.forEach(function (transaction) {
      totals[transaction.month] = (totals[transaction.month] || 0) + transaction.amount;
    });
    var months = (statementMonths || []).filter(function (month) {
      return totals[month] > 0;
    }).sort();
    if (!months.length) return null;
    var latestMonth = months[months.length - 1];
    var latestYear = parseInt(latestMonth.slice(0, 4), 10);
    var elapsedMonths = parseInt(latestMonth.slice(5, 7), 10);
    var lookback = months.slice(-12);
    var streams = {};
    salaryRows.forEach(function (transaction) {
      if (lookback.indexOf(transaction.month) === -1) return;
      var key = salaryStreamKey(transaction.description);
      if (!key) return;
      if (!streams[key]) streams[key] = {};
      if (!streams[key][transaction.month]) streams[key][transaction.month] = [];
      streams[key][transaction.month].push(transaction.amount);
    });
    var recurringKeys = Object.keys(streams).filter(function (key) {
      return Object.keys(streams[key]).length >= Math.max(3, Math.ceil(lookback.length * 0.6));
    });
    var baseMonthly = recurringKeys.reduce(function (total, key) {
      var ordinaryPayments = Object.keys(streams[key]).sort().slice(-3).map(function (month) {
        return Math.min.apply(null, streams[key][month]);
      });
      return total + median(ordinaryPayments);
    }, 0);
    if (!baseMonthly) {
      baseMonthly = median(months.slice(-6).map(function (month) { return totals[month]; }));
    }

    var ytdMonths = months.filter(function (month) {
      return parseInt(month.slice(0, 4), 10) === latestYear &&
        parseInt(month.slice(5, 7), 10) <= elapsedMonths;
    });
    var ytd = ytdMonths.reduce(function (total, month) { return total + totals[month]; }, 0);
    var priorYtd = months.filter(function (month) {
      return parseInt(month.slice(0, 4), 10) === latestYear - 1 &&
        parseInt(month.slice(5, 7), 10) <= elapsedMonths;
    }).reduce(function (total, month) { return total + totals[month]; }, 0);

    var byYear = {};
    months.forEach(function (month) {
      var year = month.slice(0, 4);
      if (!byYear[year]) byYear[year] = [];
      byYear[year].push(month);
    });
    var completeYears = Object.keys(byYear).filter(function (year) {
      return parseInt(year, 10) < latestYear && byYear[year].length === 12;
    }).sort().slice(-2);
    var yearBases = {};
    completeYears.forEach(function (year) {
      var values = byYear[year].map(function (month) { return totals[month]; })
        .sort(function (left, right) { return left - right; });
      yearBases[year] = median(values.slice(0, Math.max(1, Math.ceil(values.length * 0.75))));
    });
    var bonusPatterns = [];
    if (completeYears.length >= 2) {
      for (var monthNumber = 1; monthNumber <= 12; monthNumber += 1) {
        var ratios = completeYears.map(function (year) {
          var key = year + "-" + ("0" + monthNumber).slice(-2);
          return Math.max(0, (totals[key] || 0) / yearBases[year] - 1);
        });
        if (ratios.every(function (ratio) { return ratio >= 0.25; })) {
          bonusPatterns.push({
            month: monthNumber,
            ratios: ratios,
            medianRatio: median(ratios),
            lowRatio: Math.min.apply(null, ratios),
            highRatio: Math.max.apply(null, ratios)
          });
        }
      }
    }
    var remainingMonths = Math.max(0, 12 - elapsedMonths);
    var futurePatterns = bonusPatterns.filter(function (pattern) {
      return pattern.month > elapsedMonths;
    });
    var forecastFloor = ytd + baseMonthly * remainingMonths;
    var expectedFutureBonus = futurePatterns.reduce(function (total, pattern) {
      return total + baseMonthly * pattern.medianRatio;
    }, 0);
    var highFutureBonus = futurePatterns.reduce(function (total, pattern) {
      return total + baseMonthly * pattern.highRatio;
    }, 0);
    return {
      latestMonth: latestMonth,
      latestYear: latestYear,
      elapsedMonths: elapsedMonths,
      remainingMonths: remainingMonths,
      ytd: ytd,
      priorYtd: priorYtd,
      yoy: priorYtd ? (ytd / priorYtd - 1) * 100 : null,
      baseMonthly: baseMonthly,
      baseStreams: recurringKeys.length,
      bonusReceivedYtd: Math.max(0, ytd - baseMonthly * elapsedMonths),
      completeYears: completeYears,
      bonusPatterns: bonusPatterns,
      futurePatterns: futurePatterns,
      forecastFloor: forecastFloor,
      expectedFutureBonus: expectedFutureBonus,
      forecastCentral: forecastFloor + expectedFutureBonus,
      forecastHigh: forecastFloor + highFutureBonus
    };
  }
  function spendable(txs) { return txs.filter(function (t) { return !EXCLUDED[t.category]; }); }
  function sum(list, fn) {
    var total = 0;
    list.forEach(function (x) { total += fn ? fn(x) : x; });
    return total;
  }
  function pct(a, b) { return b === 0 ? null : ((a - b) / Math.abs(b)) * 100; }
  // Annual premiums land as single huge charges, so a mean baseline is useless
  // for "is this month normal?" questions. Median ignores those spikes.
  function median(values) {
    if (!values.length) return 0;
    var s = values.slice().sort(function (a, b) { return a - b; });
    var mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  // Merchant grouping is the ledger's, not a second opinion: this module had
  // its own normalizer with its own alias table, so "Group purchases" and the
  // habit insight could name and count the same merchant differently. Both now
  // key on FinanceGrouping.merchantKey and label with FinanceGrouping's
  // merchantDisplayName. The aliases that table carried are gone with it -
  // "Grab" survives because the shared key already canonicalizes it, and the
  // rest are whatever the ledger calls them. (The Python owner-rule key in
  // scripts/build_data.py is a different concept and is untouched.)
  //
  // Same precedence the ledger's grouped rows use: a name you set yourself
  // outranks statement text, and the most common name in the group wins, ties
  // broken by sort order so an unrelated edit cannot retitle a group.
  function dominant(counts) {
    return Object.keys(counts).sort(function (left, right) {
      return counts[right] - counts[left] || left.localeCompare(right);
    })[0] || null;
  }
  function countName(counts, name) {
    counts[name] = (counts[name] || 0) + 1;
  }
  function transactionLabel(transaction) {
    var shopeeItems = transaction.shopee && transaction.shopee.items;
    var grabReceipt = transaction.grab && transaction.grab.receipts &&
      transaction.grab.receipts[0];
    var grabLabel = grabReceipt && (
      ((grabReceipt.category === "Food & dining" || grabReceipt.category === "Groceries") &&
        grabReceipt.merchant && grabReceipt.merchant !== "Grab"
        ? grabReceipt.merchant
        : null) ||
      (grabReceipt.pickup && grabReceipt.dropoff
        ? (grabReceipt.pickupLabel || grabReceipt.pickup) + " → " +
          (grabReceipt.dropoffLabel || grabReceipt.dropoff)
        : null) ||
      grabReceipt.merchant || grabReceipt.service
    );
    return transaction.displayName ||
      (Array.isArray(shopeeItems) && shopeeItems.length ? shopeeItems[0] : null) ||
      grabLabel ||
      window.FinanceGrouping.merchantDisplayName(transaction.description);
  }

  // build_data.py emits no per-account monthly rollup, so the account-level
  // findings derive their own from the rows the dashboard already holds:
  // interest and salary arrive as deposits, card bills leave as withdrawals.
  function accountMonthTotals(account, throughMonth) {
    var months = [];
    var byMonth = {};
    if (!account || !account.transactions) return { months: months, byMonth: byMonth };
    (account.months || []).forEach(function (month) {
      if (month > throughMonth || byMonth[month]) return;
      months.push(month);
      byMonth[month] = { interest: 0, income: 0, ccBills: 0 };
    });
    account.transactions.forEach(function (t) {
      var row = byMonth[t.month];
      if (!row) return;
      if (t.direction === "deposit" && t.flow === "Interest") row.interest += t.amount;
      else if (t.direction === "deposit" && t.flow === "Salary") row.income += t.amount;
      else if (t.direction === "withdrawal" && t.flow === "Credit card bill") {
        row.ccBills += t.amount;
      }
    });
    months.sort();
    return { months: months, byMonth: byMonth };
  }

  function monthlySpend(data, month) {
    return sum(spendable(data.transactions.filter(function (t) { return t.month === month; })), signed);
  }
  function catMonth(data, month, category) {
    return sum(data.transactions.filter(function (t) {
      return t.month === month && t.category === category && !EXCLUDED[t.category];
    }), signed);
  }

  function summarize(data, currentMonth) {
    var months = data.months || [];
    var idx = months.indexOf(currentMonth);
    if (idx < 0) {
      return { kind: "info", text: "Choose a statement month to compare spending." };
    }

    var current = monthlySpend(data, currentMonth);
    var baselineMonths = months.slice(Math.max(0, idx - 6), idx);
    if (!baselineMonths.length) {
      return {
        kind: "info",
        text: "You spent " + money(current) + " in " + label(currentMonth) +
          ". More statement history is needed before changes can be compared reliably."
      };
    }

    var typical = median(baselineMonths.map(function (month) {
      return monthlySpend(data, month);
    }));
    if (typical <= 0) {
      return {
        kind: "info",
        text: "You spent " + money(current) + " in " + label(currentMonth) +
          ". The recent baseline is too small for a meaningful percentage comparison."
      };
    }

    var difference = current - typical;
    var change = (difference / typical) * 100;
    var direction = Math.abs(change) < 5 ? "steady" : change > 0 ? "higher" : "lower";
    var text = direction === "steady"
      ? "Spending held broadly steady at " + money(current) + " in " + label(currentMonth) +
        ", versus a recent typical month of " + money(typical) + "."
      : "Spending was " + money(current) + " in " + label(currentMonth) + ", " +
        Math.abs(change).toFixed(0) + "% " + direction +
        " than your recent typical month of " + money(typical) + ".";

    var categories = {};
    spendable(data.transactions).forEach(function (transaction) {
      if (transaction.month === currentMonth || baselineMonths.indexOf(transaction.month) !== -1) {
        categories[transaction.category] = true;
      }
    });
    var shifts = Object.keys(categories).map(function (category) {
      var now = catMonth(data, currentMonth, category);
      var normal = median(baselineMonths.map(function (month) {
        return catMonth(data, month, category);
      }));
      return { category: category, delta: now - normal };
    });
    var matchingDirection = shifts.filter(function (shift) {
      return difference >= 0 ? shift.delta > 0 : shift.delta < 0;
    }).sort(function (left, right) {
      return Math.abs(right.delta) - Math.abs(left.delta);
    });
    var driver = matchingDirection[0];
    if (driver && Math.abs(driver.delta) >= 20) {
      text += " " + driver.category + " was the main driver, " +
        (driver.delta > 0 ? "up " : "down ") + money(driver.delta) +
        " from its recent norm.";
    }

    var biggest = spendable(data.transactions.filter(function (transaction) {
      return transaction.month === currentMonth && transaction.type === "debit";
    })).sort(function (left, right) { return right.amount - left.amount; })[0];
    if (biggest && biggest.amount >= Math.max(150, current * 0.15)) {
      text += " The largest charge was " + money(biggest.amount) + " for " +
        transactionLabel(biggest) + ".";
    }
    return {
      kind: Math.abs(change) < 5 ? "info" : change > 0 ? "warn" : "good",
      text: text
    };
  }

  var WEALTH = { "Investment": 1, "Retirement (SRS)": 1, "Fixed deposit": 1 };
  var INTEREST_BASELINE_MIN = 5;

  function accountInsights(data, currentMonth, account, out) {
    if (!account || !account.transactions.length) return;
    // A historical selection must never leak later account activity into a
    // finding whose heading names the selected month.
    var months = account.months.filter(function (m) { return m <= currentMonth; });
    var year = currentMonth.slice(0, 4);
    var investedYear = 0, investedAll = 0, salaryYear = 0;
    var byMonth = {};
    account.transactions.forEach(function (t) {
      if (t.month > currentMonth) return;
      if (t.direction === "withdrawal" && WEALTH[t.flow]) {
        investedAll += t.amount;
        byMonth[t.month] = (byMonth[t.month] || 0) + t.amount;
        if (t.month.slice(0, 4) === year) investedYear += t.amount;
      }
      if (t.direction === "deposit" && t.flow === "Salary" && t.month.slice(0, 4) === year) {
        salaryYear += t.amount;
      }
    });

    if (investedYear > 0 && salaryYear > 0) {
      out.push({
        kind: "good",
        icon: "invest",
        scope: "yearly",
        title: "You moved " + money(investedYear) + " into investments in " + year,
        detail: "That is " + ((investedYear / salaryYear) * 100).toFixed(0) +
          "% of the " + money(salaryYear) + " salary credited this year. Transfers out, not spending.",
      });
    }

    var active = months.filter(function (m) { return byMonth[m]; });
    if (active.length >= 6) {
      var recent = months.slice(-6).filter(function (m) { return byMonth[m]; });
      var avg = 0;
      recent.forEach(function (m) { avg += byMonth[m]; });
      avg = avg / (recent.length || 1);
      out.push({
        kind: "info",
        icon: "repeat",
        scope: "monthly",
        title: "Investing about " + money(avg) + " a month",
        detail: money(investedAll) + " moved across " + active.length +
          " months of statements. Recent months have been steady.",
      });
    }

    var current = byMonth[currentMonth];
    if (current) {
      var rows = account.transactions.filter(function (t) {
        return t.month === currentMonth && t.direction === "withdrawal" && WEALTH[t.flow];
      });
      out.push({
        kind: "info",
        icon: "invest",
        scope: "monthly",
        title: label(currentMonth) + ": " + money(current) + " invested",
        detail: rows.length + " transfer" + (rows.length === 1 ? "" : "s") + " — " +
          rows.map(function (r) { return money(r.amount) + " on " + r.date.slice(8) + " " +
            MONTH_NAMES[parseInt(r.date.slice(5, 7), 10) - 1]; }).join(", ") + ".",
      });
    }
  }

  function build(data, currentMonth, account) {
    var out = [];
    var months = data.months;
    var idx = months.indexOf(currentMonth);
    var recent = months.slice(Math.max(0, idx - 5), idx + 1);
    var prior = months.slice(Math.max(0, idx - 17), Math.max(0, idx - 5));

    // 1. Salary steps
    var steps = (data.salarySteps || []).filter(function (s) {
      return s.from <= currentMonth;
    });
    if (steps.length >= 2) {
      var last = steps[steps.length - 1];
      var prev = steps[steps.length - 2];
      var change = pct(last.amount, prev.amount);
      if (change !== null && Math.abs(change) >= 0.5) {
        var down = change < 0;
        out.push({
          kind: down ? "warn" : "good",
          icon: down ? "down" : "up",
          scope: "yearly",
          title: "Salary " + (down ? "stepped down" : "rose") + " " + Math.abs(change).toFixed(1) + "% in " + label(last.from),
          detail: money(prev.amount) + " to " + money(last.amount) + " a month" +
            (down ? ", the first drop after " + (steps.length - 1) + " recorded changes." : "."),
        });
      }
    }

    // 2. Category baselines: recent 6-month average vs the 12 months before
    var recentBy = {}, priorBy = {};
    spendable(data.transactions).forEach(function (t) {
      if (recent.indexOf(t.month) !== -1) recentBy[t.category] = (recentBy[t.category] || 0) + signed(t);
      else if (prior.indexOf(t.month) !== -1) priorBy[t.category] = (priorBy[t.category] || 0) + signed(t);
    });
    var shifts = [];
    Object.keys(recentBy).forEach(function (cat) {
      var recentVals = recent.map(function (m) { return catMonth(data, m, cat); });
      var priorVals = prior.map(function (m) { return catMonth(data, m, cat); });
      var a = median(recentVals);
      var b = median(priorVals);
      if (a < 60 && b < 60) return;
      var change = pct(a, b);
      if (change === null || Math.abs(change) < 25 || Math.abs(a - b) < 40) return;
      shifts.push({ cat: cat, a: a, b: b, change: change, weight: Math.abs(a - b) });
    });
    shifts.sort(function (x, y) { return y.weight - x.weight; });
    shifts.slice(0, 4).forEach(function (s) {
      var up = s.change > 0;
      out.push({
        kind: up ? "warn" : "good",
        icon: up ? "up" : "down",
        scope: "monthly",
        category: s.cat,
        title: s.cat + " is running " + (up ? "up" : "down") + " " + Math.abs(s.change).toFixed(0) + "%",
        detail: "Typically " + money(s.a) + " a month over the last " + recent.length +
          " months, against " + money(s.b) + " before that.",
      });
    });

    // 3. Habits: repeat merchants annualised
    var last12 = months.slice(Math.max(0, idx - 11), idx + 1);
    var byMerchant = {};
    spendable(data.transactions).forEach(function (t) {
      if (last12.indexOf(t.month) === -1 || t.type !== "debit") return;
      var k = t.merchantKey || window.FinanceGrouping.merchantKey(t.description);
      if (!byMerchant[k]) {
        byMerchant[k] = {
          total: 0, count: 0, months: {}, ids: [],
          displayNames: {}, statementNames: {}
        };
      }
      byMerchant[k].total += t.amount;
      byMerchant[k].count += 1;
      byMerchant[k].months[t.month] = true;
      // The label the ledger shows never appears verbatim in statement text, so
      // a free-text drill-down found only a fraction of these rows. Carry the
      // matched ids and filter on them exactly.
      byMerchant[k].ids.push(t.id);
      // Only a name the user typed labels the habit; a per-charge booking
      // name from a Trip.com match would title the whole merchant after one
      // arbitrary hotel.
      var userName = window.FinanceGrouping.userDisplayName(t);
      if (userName) countName(byMerchant[k].displayNames, userName);
      else countName(byMerchant[k].statementNames,
        window.FinanceGrouping.merchantDisplayName(t.description));
    });
    var habits = Object.keys(byMerchant).map(function (k) {
      var m = byMerchant[k];
      return {
        name: dominant(m.displayNames) || dominant(m.statementNames) || k,
        total: m.total, count: m.count,
        months: Object.keys(m.months).length, ids: m.ids
      };
    }).filter(function (h) { return h.months >= 6 && h.count >= 12; });
    habits.sort(function (a, b) { return b.total - a.total; });
    habits.slice(0, 3).forEach(function (h) {
      // The window shrinks near the start of the data; dividing by a
      // hard-coded 12 understated the monthly figure and mislabelled the span.
      out.push({
        kind: "info",
        icon: "repeat",
        scope: "yearly",
        title: h.name + ": " + money(h.total) + " over " + last12.length +
          (last12.length === 1 ? " month" : " months"),
        detail: h.count + " charges averaging " + money(h.total / h.count) + ", about " +
          money(h.total / last12.length) + " a month.",
        ids: h.ids,
        filterLabel: h.name,
      });
    });

    // 4. Account-level: interest trend and savings rate
    var accountTotals = accountMonthTotals(account, currentMonth);
    var acctMonths = accountTotals.months;
    var acct = accountTotals.byMonth;
    if (acctMonths.length >= 6) {
      var first = acct[acctMonths[0]], latest = acct[acctMonths[acctMonths.length - 1]];
      // A few cents of interest in the first month makes any later figure a
      // four-digit percentage. Compare only from a baseline worth talking about.
      if (first && latest && first.interest >= INTEREST_BASELINE_MIN) {
        var drop = pct(latest.interest, first.interest);
        if (drop !== null && Math.abs(drop) >= 20) {
          out.push({
            kind: "info",
            icon: "percent",
            scope: "yearly",
            title: "Interest earned is " + (drop < 0 ? "down" : "up") + " " + Math.abs(drop).toFixed(0) + "%",
            detail: money(first.interest) + " in " + label(acctMonths[0]) + " to " +
              money(latest.interest) + " in " + label(acctMonths[acctMonths.length - 1]) +
              " as the account balance moved out.",
          });
        }
      }
      var totalIncome = 0, totalCards = 0;
      acctMonths.slice(-12).forEach(function (m) {
        totalIncome += acct[m].income || 0;
        totalCards += Math.abs(acct[m].ccBills || 0);
      });
      if (totalIncome > 0) {
        out.push({
          kind: "info",
          icon: "wallet",
          scope: "yearly",
          title: "Card spending is " + ((totalCards / totalIncome) * 100).toFixed(0) + "% of income",
          detail: money(totalCards) + " billed against " + money(totalIncome) +
            " credited over the last " + Math.min(12, acctMonths.length) + " months. The rest stayed in the account or moved out as withdrawals.",
        });
      }
    }

    // 5. This month vs its own baseline
    if (idx >= 3) {
      var thisSpend = monthlySpend(data, currentMonth);
      var base = months.slice(Math.max(0, idx - 6), idx);
      var avg = median(base.map(function (m) { return monthlySpend(data, m); }));
      var delta = pct(thisSpend, avg);
      if (delta !== null && Math.abs(delta) >= 15) {
        var over = delta > 0;
        var cats = {};
        spendable(data.transactions.filter(function (t) { return t.month === currentMonth; }))
          .forEach(function (t) { cats[t.category] = (cats[t.category] || 0) + signed(t); });
        var driver = Object.keys(cats).map(function (c) {
          return { c: c, d: cats[c] - median(base.map(function (m) { return catMonth(data, m, c); })) };
        }).sort(function (a, b) { return Math.abs(b.d) - Math.abs(a.d); })[0];
        out.push({
          kind: over ? "warn" : "good",
          icon: over ? "up" : "down",
          scope: "monthly",
          title: label(currentMonth) + " ran " + Math.abs(delta).toFixed(0) + "% " +
            (over ? "above" : "below") + " your typical month",
          detail: money(thisSpend) + " against a typical " + money(avg) +
            (driver ? ". Biggest mover: " + driver.c + " " + (driver.d > 0 ? "+" : "-") + money(driver.d) + "." : "."),
        });
      }
      // Largest single charge this month
      var biggest = spendable(data.transactions.filter(function (t) {
        return t.month === currentMonth && t.type === "debit";
      })).sort(function (a, b) { return b.amount - a.amount; })[0];
      if (biggest && biggest.amount >= 150) {
        var biggestName = transactionLabel(biggest);
        out.push({
          kind: "info",
          icon: "receipt",
          scope: "monthly",
          title: "Largest charge: " + money(biggest.amount) + " at " + biggestName,
          detail: biggest.description + " on " + (biggest.date || currentMonth) + ", filed under " + biggest.category + ".",
          ids: [biggest.id],
          filterLabel: biggestName,
        });
      }
    }

    // 6. Current settlement position with Yx.
    // Same calculation the Split tab uses, but cut off at the selected month.
    // The Split tab covers a whole year, so the two figures legitimately
    // differ — every label below names the time scope that produced it.
    var year = currentMonth.slice(0, 4);
    var position = window.FinanceGrouping.settlementPosition(
      data.transactions, data.settlements, year, currentMonth,
      { excludedCategories: EXCLUDED });
    var owed = position.newYxShare;
    var netPosition = position.netPosition;
    if (position.hasOpening || owed > 0) {
      out.push({
        kind: "info",
        icon: "users",
        scope: "yearly",
        title: position.hasOpening
          ? (netPosition >= 0 ? "Yx owes you " : "You owe Yx ") +
            settlementMoney(netPosition) + " " + position.scopeLabel
          : "Yx's share " + position.scopeLabel + ": " + money(owed),
        detail: position.hasOpening
          ? "You started " + year + " owing Yx " + settlementMoney(position.openingYouOwe) +
            (position.carried
              ? " (carried forward from the " + position.carriedFromYear + " closing balance)"
              : "") +
            ". " + settlementMoney(owed) +
            " of new Yx share" +
            (position.paidToYx ? " and " + settlementMoney(position.paidToYx) + " paid to Yx" : "") +
            " reduced that balance" +
            (position.receivedFromYx ? "; " + settlementMoney(position.receivedFromYx) +
              " received from Yx increased it" : "") +
            ". Counted " + position.scopeLabel +
            "; the Split tab shows the full year."
          : money(position.sharedTotal) + " of shared spending split in half" +
            (position.yxDirect > 0
              ? ", plus " + money(position.yxDirect) + " billed directly to her." : ".") +
            " Counted " + position.scopeLabel + ". See the Split tab.",
      });
    }

    accountInsights(data, currentMonth, account, out);

    var rank = { warn: 0, good: 1, info: 2 };
    function rankOf(item) {
      return item.kind in rank ? rank[item.kind] : 3;
    }
    out.sort(function (a, b) { return rankOf(a) - rankOf(b); });
    return out;
  }

  // merchantKey is no longer part of the public surface: the one normalizer
  // now lives in FinanceGrouping, and nothing outside this module used it.
  return {
    build: build,
    summarize: summarize,
    money: money,
    monthLabel: label,
    travelCountry: travelCountry,
    travelCity: travelCity,
    confirmedTravelCharges: confirmedTravelCharges,
    destinations: destinations,
    suggestedDestination: suggestedDestination,
    parseTravelDate: parseTravelDate,
    travelWindow: travelWindow,
    buildTrips: buildTrips,
    incomeForecast: incomeForecast
  };
})();
