(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.FinanceGrouping = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var SETTLEMENT_EXCLUDED = { Payment: true, Rebates: true };
  var MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function roundMoney(value) {
    var sign = value < 0 ? -1 : 1;
    return sign * Math.round(Math.abs(value) * 100 + 1e-9) / 100;
  }

  function signed(transaction) {
    return transaction.type === "debit" ? transaction.amount : -transaction.amount;
  }

  function monthLabel(key) {
    var parts = String(key).split("-");
    return (MONTH_NAMES[parseInt(parts[1], 10) - 1] || parts[1]) + " " + parts[0];
  }

  var CITY_SUFFIXES = ["SINGAPORE", "PETALING JAYA", "JOHOR BAHRU"];

  // A statement line is cut to a fixed width, so the trailing city arrives
  // truncated ("... SINGAPO", "... PETALING JAY") and the same merchant would
  // otherwise split across group keys. Only a *prefix* of a known city is
  // stripped, so SINGTEL, SINGLIFE and PLAZA SINGAPURA survive intact.
  function matchesCitySuffix(tail, city) {
    for (var i = 0; i < tail.length; i += 1) {
      var word = city[i];
      if (i < tail.length - 1) {
        if (tail[i] !== word) return false;
        continue;
      }
      // The final token is the truncated one. With no earlier city word to
      // anchor it, demand four characters so a real word is not mistaken for
      // a truncation; once "PETALING" has matched, "JAY" is unambiguous.
      var minimum = i === 0 ? Math.min(4, word.length) : 1;
      if (tail[i].length < minimum || word.indexOf(tail[i]) !== 0) return false;
    }
    return true;
  }

  function stripTrailingNoise(key) {
    var tokens = key.split(" ").filter(Boolean);
    var changed = true;
    while (changed && tokens.length > 1) {
      changed = false;
      // Single letters are the leftovers of a word the statement cut short.
      while (tokens.length > 1 && tokens[tokens.length - 1].length === 1) {
        tokens.pop();
        changed = true;
      }
      for (var i = 0; i < CITY_SUFFIXES.length && !changed; i += 1) {
        var city = CITY_SUFFIXES[i].split(" ");
        var longest = Math.min(city.length, tokens.length - 1);
        for (var take = longest; take >= 1; take -= 1) {
          if (matchesCitySuffix(tokens.slice(tokens.length - take), city)) {
            tokens = tokens.slice(0, tokens.length - take);
            changed = true;
            break;
          }
        }
      }
    }
    return tokens.join(" ");
  }

  function merchantKey(description) {
    var key = String(description || "").toUpperCase();
    if (/^SUBSCRIPTIONGRAB(?:\*|\s|-|$)/.test(key)) return "GRAB SUBSCRIPTION";
    if (/^GRAB(?:\*|\s|-|$)/.test(key)) return "GRAB";
    if (/^NTUC\s+(?:FAIRPRICE\b|FP(?:\b|-))/.test(key)) return "NTUC FAIRPRICE";
    key = key.replace(/GPC-[0-9A-Z]+/g, "");
    key = key.replace(/\b(?:A-)?(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])[A-Z0-9]{8,}\b/g, "");
    key = key.replace(/[0-9]{4,}/g, "");
    key = key.replace(/[^A-Z ]+/g, " ");
    key = key.replace(/\b(?:SINGAPORE|PETALING JAYA|JOHOR BAHRU)\b/g, " ");
    key = stripTrailingNoise(key.trim().replace(/\s+/g, " "));
    return key || String(description || "").toUpperCase().trim();
  }

  function merchantLabel(transaction) {
    if (transaction.displayName) return transaction.displayName;
    var key = merchantKey(transaction.description);
    if (key === "GRAB") return "Grab";
    if (key === "GRAB SUBSCRIPTION") return "Grab subscription";
    if (key === "NTUC FAIRPRICE") return "NTUC FairPrice";
    var label = String(transaction.description || "").replace(/GPC-[0-9A-Z]+/ig, "");
    label = label.replace(/\b(?:A-)?(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])[A-Z0-9]{8,}\b/ig, "");
    label = label.replace(/[0-9]{4,}/g, "").replace(/\s+/g, " ").trim();
    return label || transaction.description || "Unknown merchant";
  }

  function accountSourceOrder(transaction) {
    var provenance = transaction.provenance || {};
    function pad(value, width) {
      var text = String(value || 0);
      while (text.length < width) text = "0" + text;
      return text;
    }
    return String(transaction.month || "") + "|" +
      String(provenance.sourceFile || "") + "|" +
      pad(provenance.page, 4) + "|" + pad(provenance.line, 6);
  }

  function accountCounterparty(description) {
    var original = String(description || "").replace(
      /\s*Please note that you are bound\b.*$/i, "").trim();
    var upper = original.toUpperCase();
    if (/INTERACTIVE BROKERS/.test(upper)) return "Interactive Brokers";
    if (/PHILLIP SECURITIES/.test(upper)) return "Phillip Securities";
    if (/TIGER BROKERS/.test(upper)) return "Tiger Brokers";
    if (/IRAS|INLAND REVENUE/.test(upper)) return "IRAS";
    if (/SALARY PAYMENT DSTA|GIRO SALA/.test(upper)) return "DSTA salary";
    if (/SUPPLIERPYMT DSTA|GIRO SUPP/.test(upper)) return "DSTA reimbursement";
    if (/ONE BONUS INTEREST/.test(upper)) return "UOB One bonus interest";
    if (/INTEREST CREDIT/.test(upper)) return "UOB interest";
    if (/UOB CARDS?/.test(upper)) return "UOB Cards";
    if (/HSBC CC/.test(upper)) return "HSBC credit card";
    if (/SHOPEEPAY/.test(upper)) return "ShopeePay";

    var nets = original.match(/NETS Debit-Consumer\s+(.+?)(?:\d{8}|\s+x{4,}\d+)/i);
    if (nets) original = nets[1];
    original = original
      .replace(/^(?:PAYNOW-FAST|Funds Trf - FAST|Inward (?:Credit|Debit|CR|DR)-FAST)\s*/i, "")
      .replace(/^(?:PAYNOW\s+)?OTHR\s+/i, "")
      .replace(/\b(?:PIB|MBK)\d{12,}\b/ig, " ")
      .replace(/\b(?:OTHR|COLL|SALA|SUPP)\b/ig, " ")
      .replace(/\b(?:Transfer|PayNow)\s*-?\s*(?:Mobile|UEN)?\b.*$/i, " ")
      .replace(/\b(?:Q[A-Z0-9]{8,}|[A-Z0-9]*\d[A-Z0-9]{11,})\b/ig, " ")
      .replace(/\b[xX]{4,}\d+\b/g, " ")
      .replace(/\s+/g, " ").trim();
    if (!original) return "Unknown counterparty";
    return original.toLowerCase().replace(/\b\w/g, function (letter) {
      return letter.toUpperCase();
    }).replace(/\b(?:Uob|Dsta|Iras|Nets)\b/g, function (word) {
      return word.toUpperCase();
    });
  }

  function accountIsInternalMovement(transaction) {
    return {
      "Transfer": true,
      "Investment": true,
      "Retirement (SRS)": true,
      "Fixed deposit": true,
      "Credit card bill": true
    }[transaction.flow] === true;
  }

  function accountReviewWhitelisted(transaction) {
    return transaction.direction === "deposit" &&
      /^Misc Credit\b/i.test((transaction.description || "").trim());
  }

  // Confirmed-expected recipients: large or first payments to them are
  // routine, so the counterparty and amount checks stay quiet. Data-quality
  // checks (unclassified, derived amount, unreconciled source) and the
  // accidental double-payment check still apply. Matched as a case-insensitive
  // prefix of the normalized counterparty, because statements truncate
  // ("Design 4 Space Pte." and "Design 4 Space Pte L" are the same firm).
  var TRUSTED_COUNTERPARTIES = [
    "PARTNER FULL NAME",
    "Yx",
    "Design 4 Space"
  ];

  function accountTrustedCounterparty(counterparty) {
    var name = String(counterparty || "").toLowerCase();
    return TRUSTED_COUNTERPARTIES.some(function (trusted) {
      return name.indexOf(trusted.toLowerCase()) === 0;
    });
  }

  function analyzeAccountTransactions(rows, reviewedIds) {
    var reviewed = reviewedIds || {};
    var ordered = (rows || []).slice().sort(function (left, right) {
      return accountSourceOrder(left).localeCompare(accountSourceOrder(right));
    });
    var firstSeen = {};
    var duplicates = {};
    ordered.forEach(function (transaction) {
      var counterparty = accountCounterparty(transaction.description);
      var key = [transaction.date, counterparty, transaction.direction,
        Number(transaction.amount).toFixed(2)].join("|");
      if (!duplicates[key]) duplicates[key] = [];
      duplicates[key].push(transaction.id);
    });
    var analysis = {};
    ordered.forEach(function (transaction) {
      var counterparty = accountCounterparty(transaction.description);
      var first = firstSeen[counterparty] === undefined;
      firstSeen[counterparty] = true;
      var reasons = [];
      var checks = [];
      var internal = accountIsInternalMovement(transaction);
      var trusted = accountTrustedCounterparty(counterparty);
      if ((transaction.flow || "Other") === "Other" &&
          !accountReviewWhitelisted(transaction)) {
        reasons.push("Flow is still unclassified");
        checks.push("unclassified");
      }
      if (!trusted && transaction.direction === "withdrawal" &&
          transaction.flow === "Transfer" && transaction.amount >= 500) {
        reasons.push("Large transfer of S$" + Number(transaction.amount).toFixed(2));
        checks.push("large-transfer");
      } else if (!trusted && transaction.direction === "withdrawal" && !internal &&
                 transaction.amount >= 1000) {
        reasons.push("Large non-transfer withdrawal of S$" +
          Number(transaction.amount).toFixed(2));
        checks.push("large-withdrawal");
      }
      // Money arriving is never treated as suspicious - deposits only carry
      // data-quality flags (unclassified flow, derived amount, unreconciled
      // source), not amount or duplicate checks.
      if (!trusted && first && transaction.direction === "withdrawal" && !internal &&
          transaction.amount >= 500) {
        reasons.push("First sizeable payment to this counterparty");
        checks.push("new-counterparty");
      }
      var duplicateKey = [transaction.date, counterparty, transaction.direction,
        Number(transaction.amount).toFixed(2)].join("|");
      var matching = duplicates[duplicateKey] || [];
      if (transaction.direction !== "deposit" && matching.length > 1 &&
          transaction.amount * matching.length >= 40) {
        reasons.push(matching.length + " identical same-day bank movements");
        checks.push("possible-duplicate");
      }
      if (transaction.amountSource === "balance") {
        reasons.push("Amount was derived from the running balance");
        checks.push("derived-amount");
      }
      if (transaction.provenance && transaction.provenance.verified === false) {
        reasons.push("Source has not been reconciled");
        checks.push("unverified-source");
      }
      analysis[transaction.id] = {
        counterparty: counterparty,
        firstCounterparty: first,
        internalMovement: internal,
        reasons: reasons,
        checks: checks,
        requiresReview: reasons.length > 0,
        reviewed: Boolean(reviewed[transaction.id])
      };
    });
    return analysis;
  }

  function groupAccountTransactions(rows) {
    var groups = {};
    (rows || []).forEach(function (transaction) {
      var counterparty = transaction.counterparty ||
        accountCounterparty(transaction.description);
      var key = [counterparty, transaction.flow, transaction.direction].join("|");
      if (!groups[key]) {
        groups[key] = {
          label: counterparty,
          flow: transaction.flow || "Other",
          direction: transaction.direction,
          count: 0,
          amount: 0,
          lastDate: transaction.date || transaction.month,
          reviewCount: 0,
          reviewIds: []
        };
      }
      var group = groups[key];
      group.count += 1;
      group.amount += Number(transaction.amount || 0);
      if (transaction.accountReview && transaction.accountReview.requiresReview &&
          !transaction.accountReview.reviewed) {
        group.reviewCount += 1;
        group.reviewIds.push(transaction.id);
      }
      if ((transaction.date || transaction.month) > group.lastDate) {
        group.lastDate = transaction.date || transaction.month;
      }
    });
    return Object.keys(groups).map(function (key) {
      groups[key].amount = roundMoney(groups[key].amount);
      return groups[key];
    }).sort(function (left, right) {
      return Math.abs(right.amount) - Math.abs(left.amount) ||
        right.lastDate.localeCompare(left.lastDate);
    });
  }

  // Most frequent name in the group, ties broken by sort order. Last-writer-wins
  // made the visible label depend on row order, so the same group could be
  // titled differently after an unrelated edit.
  function dominantLabel(counts) {
    return Object.keys(counts).sort(function (left, right) {
      return counts[right] - counts[left] || left.localeCompare(right);
    })[0] || null;
  }

  // Every row lands in exactly one bucket, so the parts always add up to count.
  // Payment and rebate rows used to fall through all of them and render as
  // "0 purchases" next to a large amount.
  function groupCountLabel(group) {
    function plural(value, noun) {
      return value + " " + noun + (value === 1 ? "" : "s");
    }
    var parts = [];
    if (group.purchaseCount) parts.push(plural(group.purchaseCount, "purchase"));
    if (group.refundCount) parts.push(plural(group.refundCount, "refund"));
    if (group.paymentCount) parts.push(plural(group.paymentCount, "payment"));
    if (group.otherCount) parts.push(plural(group.otherCount, "row"));
    if (!parts.length) parts.push(plural(group.count || 0, "row"));
    return parts.join(" · ");
  }

  function groupPurchases(rows) {
    var groups = {};
    var displayNames = {};
    var fallbackNames = {};
    rows.forEach(function (transaction) {
      var key = merchantKey(transaction.description) + "|" +
        transaction.category + "|" + transaction.owner;
      if (!groups[key]) {
        groups[key] = {
          label: merchantLabel(transaction),
          category: transaction.category,
          owner: transaction.owner,
          lastDate: transaction.date || transaction.month,
          count: 0,
          purchaseCount: 0,
          refundCount: 0,
          paymentCount: 0,
          otherCount: 0,
          amount: 0,
          riskCount: 0
        };
        displayNames[key] = {};
        fallbackNames[key] = {};
      }
      var group = groups[key];
      group.count += 1;
      group.amount += signed(transaction);
      if (transaction.type === "debit") group.purchaseCount += 1;
      else if (transaction.type === "refund") group.refundCount += 1;
      else if (transaction.type === "payment") group.paymentCount += 1;
      else group.otherCount += 1;
      if (transaction.risk && !transaction.risk.recognized) group.riskCount += 1;
      if ((transaction.date || transaction.month) > group.lastDate) {
        group.lastDate = transaction.date || transaction.month;
      }
      if (transaction.displayName) {
        displayNames[key][transaction.displayName] =
          (displayNames[key][transaction.displayName] || 0) + 1;
      } else {
        var fallback = merchantLabel(transaction);
        fallbackNames[key][fallback] = (fallbackNames[key][fallback] || 0) + 1;
      }
    });
    return Object.keys(groups).map(function (key) {
      groups[key].amount = roundMoney(groups[key].amount);
      // A name you set yourself outranks anything derived from statement text.
      groups[key].label = dominantLabel(displayNames[key]) ||
        dominantLabel(fallbackNames[key]) || groups[key].label;
      return groups[key];
    }).sort(function (left, right) {
      return Math.abs(right.amount) - Math.abs(left.amount) ||
        right.lastDate.localeCompare(left.lastDate);
    });
  }

  // Both the summary panel and the table foot read from this, so the two
  // surfaces can no longer disagree: netCost always excludes the excluded
  // categories, and what was dropped is reported separately rather than
  // silently folded into a second, differently-computed "net".
  function summarize(rows, excludedCategories) {
    var excluded = excludedCategories || {};
    var result = {
      count: 0,
      netCost: 0,
      excludedCount: 0,
      excludedTotal: 0,
      categoryTotals: {},
      ownerTotals: {}
    };
    rows.forEach(function (transaction) {
      var value = signed(transaction);
      if (excluded[transaction.category]) {
        result.excludedCount += 1;
        result.excludedTotal += value;
        return;
      }
      result.count += 1;
      result.netCost += value;
      result.categoryTotals[transaction.category] =
        (result.categoryTotals[transaction.category] || 0) + value;
      // An owner outside the known set still needs a bucket; without one an
      // undefined tag reads back as NaN and poisons every downstream total.
      var owner = transaction.owner || "Untagged";
      result.ownerTotals[owner] = (result.ownerTotals[owner] || 0) + value;
    });
    result.netCost = roundMoney(result.netCost);
    result.excludedTotal = roundMoney(result.excludedTotal);
    Object.keys(result.categoryTotals).forEach(function (category) {
      result.categoryTotals[category] = roundMoney(result.categoryTotals[category]);
    });
    Object.keys(result.ownerTotals).forEach(function (owner) {
      result.ownerTotals[owner] = roundMoney(result.ownerTotals[owner]);
    });
    return result;
  }

  function bindToggle(button, label, onToggle, initialValue) {
    var active = Boolean(initialValue);
    function sync() {
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
      label.textContent = active ? "Show individual" : "Group purchases";
    }
    button.addEventListener("click", function () {
      active = !active;
      sync();
      onToggle(active);
    });
    sync();
    // The page needs to drive the toggle too — a drill-down that resets the
    // other filters has to reset this one, and the closure state must follow
    // or the next click would flip back to where it already was.
    return {
      isActive: function () { return active; },
      set: function (value) {
        var next = Boolean(value);
        if (next === active) return false;
        active = next;
        sync();
        return true;
      }
    };
  }

  function averageForMonths(rows, months, excludedCategories) {
    if (!months.length) return null;
    var totals = {};
    months.forEach(function (month) { totals[month] = 0; });
    rows.forEach(function (transaction) {
      if (totals[transaction.month] === undefined ||
          excludedCategories[transaction.category]) return;
      totals[transaction.month] += signed(transaction);
    });
    var total = months.reduce(function (sum, month) {
      return sum + totals[month];
    }, 0);
    return roundMoney(total / months.length);
  }

  // ---------- Settlement position with Yx ----------
  //
  // One implementation for both the Overview insight (scoped to the selected
  // month) and the Split tab (scoped to a whole year). Sign convention:
  // netPosition is positive when Yx owes you and negative when you owe Yx,
  // which is the inverse of the youOweYx figure recorded in settlements.json.

  function settlementActivity(transactions, fromMonth, toMonth, excluded) {
    var byMonth = {};
    var ownerTotals = { Nic: 0, Shared: 0, Yx: 0, Untagged: 0 };
    (transactions || []).forEach(function (transaction) {
      var month = transaction.month;
      if (!month || month < fromMonth || month > toMonth) return;
      if (excluded[transaction.category]) return;
      if (!byMonth[month]) byMonth[month] = { Nic: 0, Shared: 0, Yx: 0, Untagged: 0 };
      var owner = transaction.owner;
      if (byMonth[month][owner] === undefined) byMonth[month][owner] = 0;
      if (ownerTotals[owner] === undefined) ownerTotals[owner] = 0;
      byMonth[month][owner] += signed(transaction);
      ownerTotals[owner] += signed(transaction);
    });
    // Settle in currency cents per statement month, so an odd-cent shared total
    // stays explicit and the displayed months add up to the yearly figure.
    var months = Object.keys(byMonth).sort();
    var sharedHalf = 0;
    var newYxShare = 0;
    months.forEach(function (month) {
      var half = roundMoney(byMonth[month].Shared / 2);
      byMonth[month].sharedHalf = half;
      byMonth[month].yxShare = roundMoney(half + byMonth[month].Yx);
      sharedHalf += half;
      newYxShare += half + byMonth[month].Yx;
    });
    Object.keys(ownerTotals).forEach(function (owner) {
      ownerTotals[owner] = roundMoney(ownerTotals[owner]);
    });
    return {
      months: months,
      byMonth: byMonth,
      ownerTotals: ownerTotals,
      sharedTotal: ownerTotals.Shared,
      sharedHalf: roundMoney(sharedHalf),
      yxDirect: ownerTotals.Yx,
      newYxShare: roundMoney(newYxShare)
    };
  }

  function settlementPaymentTotals(payments, fromMonth, toMonth) {
    var paidToYx = 0;
    var receivedFromYx = 0;
    var paidToYxCount = 0;
    var receivedFromYxCount = 0;
    (payments || []).forEach(function (payment) {
      if (!payment || !payment.date) return;
      var month = String(payment.date).slice(0, 7);
      if (month < fromMonth || month > toMonth) return;
      if (payment.direction === "toYx") {
        paidToYx += Number(payment.amount || 0);
        paidToYxCount += 1;
      } else if (payment.direction === "fromYx") {
        receivedFromYx += Number(payment.amount || 0);
        receivedFromYxCount += 1;
      }
    });
    return {
      paidToYx: roundMoney(paidToYx),
      receivedFromYx: roundMoney(receivedFromYx),
      paidToYxCount: paidToYxCount,
      receivedFromYxCount: receivedFromYxCount
    };
  }

  function settlementPosition(transactions, settlements, year, throughMonth, options) {
    var config = options || {};
    var excluded = config.excludedCategories || SETTLEMENT_EXCLUDED;
    var openings = (settlements &&
      (settlements.openingBalances || settlements.openings)) || [];
    var payments = (settlements && settlements.payments) || [];
    var displayYear = String(year);
    var yearStart = displayYear + "-01";
    var yearEnd = displayYear + "-12";
    var cutoff = throughMonth ? String(throughMonth) : null;
    var endMonth = cutoff && cutoff < yearEnd ? cutoff : yearEnd;

    // The applicable opening balance is the most recent one recorded at or
    // before the end of the displayed scope. Comparing against endMonth (not
    // just the year) keeps an opening recorded after the cutoff month from
    // being applied to a view that stops before it exists.
    var opening = null;
    openings.forEach(function (item) {
      if (!item || !item.from) return;
      if (String(item.from) > endMonth) return;
      if (!opening || String(item.from) > String(opening.from)) opening = item;
    });

    var openingFrom = opening ? String(opening.from) : null;
    var openingYouOwe = opening ? roundMoney(Number(opening.youOweYx || 0)) : 0;
    var carriedFromYear = null;
    if (opening) {
      // Roll the recorded balance forward through every completed year between
      // the record and the displayed year. Without this, a year with no opening
      // entry of its own would report a zero position and lose the debt.
      var openingYear = openingFrom.slice(0, 4);
      for (var step = Number(openingYear); step < Number(displayYear); step += 1) {
        var stepYear = String(step);
        var stepStart = stepYear === openingYear && openingFrom > stepYear + "-01"
          ? openingFrom : stepYear + "-01";
        var stepEnd = stepYear + "-12";
        var stepActivity = settlementActivity(transactions, stepStart, stepEnd, excluded);
        var stepPayments = settlementPaymentTotals(payments, stepStart, stepEnd);
        openingYouOwe = roundMoney(openingYouOwe - stepActivity.newYxShare -
          stepPayments.paidToYx + stepPayments.receivedFromYx);
        carriedFromYear = stepYear;
      }
    }

    // Months before the recorded opening are already inside that balance.
    var startMonth = openingFrom && openingFrom.slice(0, 4) === displayYear &&
      openingFrom > yearStart ? openingFrom : yearStart;
    var activity = settlementActivity(transactions, startMonth, endMonth, excluded);
    var paymentTotals = settlementPaymentTotals(payments, startMonth, endMonth);
    var netPosition = roundMoney(activity.newYxShare + paymentTotals.paidToYx -
      paymentTotals.receivedFromYx - openingYouOwe);
    var lastMonth = activity.months.length
      ? activity.months[activity.months.length - 1] : null;
    var scopeLabel;
    if (cutoff && cutoff >= yearStart && cutoff <= yearEnd) {
      scopeLabel = "through " + monthLabel(cutoff);
    } else if (lastMonth && lastMonth < yearEnd) {
      scopeLabel = "full year " + displayYear + " to date";
    } else {
      scopeLabel = "full year " + displayYear;
    }

    return {
      year: displayYear,
      throughMonth: cutoff,
      startMonth: startMonth,
      endMonth: endMonth,
      lastMonth: lastMonth,
      scopeLabel: scopeLabel,
      months: activity.months,
      byMonth: activity.byMonth,
      ownerTotals: activity.ownerTotals,
      sharedTotal: activity.sharedTotal,
      sharedHalf: activity.sharedHalf,
      yxDirect: activity.yxDirect,
      newYxShare: activity.newYxShare,
      paidToYx: paymentTotals.paidToYx,
      receivedFromYx: paymentTotals.receivedFromYx,
      paidToYxCount: paymentTotals.paidToYxCount,
      receivedFromYxCount: paymentTotals.receivedFromYxCount,
      // hasOpening false means the running position is unknown, not zero.
      hasOpening: Boolean(opening),
      openingFrom: openingFrom,
      openingNote: opening && opening.note ? opening.note : "",
      openingYouOwe: openingYouOwe,
      carried: Boolean(carriedFromYear),
      carriedFromYear: carriedFromYear,
      netPosition: netPosition,
      youOweYx: roundMoney(-netPosition)
    };
  }

  function summarizeAccount(rows) {
    var result = {
      count: rows.length,
      deposits: 0,
      withdrawals: 0,
      netMovement: 0,
      nonTransferSpending: 0,
      withdrawalFlows: {},
      spendingFlows: {},
      openingBalance: null,
      closingBalance: null,
      latestBalance: null,
      reconciliationGap: null
    };
    var ordered = rows.slice().sort(function (left, right) {
      return accountSourceOrder(left).localeCompare(accountSourceOrder(right));
    });
    ordered.forEach(function (transaction) {
      if (transaction.direction === "deposit") result.deposits += transaction.amount;
      else result.withdrawals += transaction.amount;
      if (transaction.direction === "withdrawal") {
        result.withdrawalFlows[transaction.flow] =
          (result.withdrawalFlows[transaction.flow] || 0) + transaction.amount;
        if (!accountIsInternalMovement(transaction)) {
          result.nonTransferSpending += transaction.amount;
          result.spendingFlows[transaction.flow] =
            (result.spendingFlows[transaction.flow] || 0) + transaction.amount;
        }
      }
    });
    if (ordered.length) {
      var first = ordered[0];
      var last = ordered[ordered.length - 1];
      var firstDelta = first.direction === "deposit" ? first.amount : -first.amount;
      result.openingBalance = roundMoney(first.balance - firstDelta);
      result.closingBalance = roundMoney(last.balance);
      result.latestBalance = {
        date: last.date,
        amount: result.closingBalance,
        sourceOrder: accountSourceOrder(last)
      };
    }
    result.deposits = roundMoney(result.deposits);
    result.withdrawals = roundMoney(result.withdrawals);
    result.netMovement = roundMoney(result.deposits - result.withdrawals);
    result.nonTransferSpending = roundMoney(result.nonTransferSpending);
    if (result.openingBalance !== null && result.closingBalance !== null) {
      result.reconciliationGap = roundMoney(
        result.closingBalance - (result.openingBalance + result.netMovement));
    }
    Object.keys(result.withdrawalFlows).forEach(function (flow) {
      result.withdrawalFlows[flow] = roundMoney(result.withdrawalFlows[flow]);
    });
    Object.keys(result.spendingFlows).forEach(function (flow) {
      result.spendingFlows[flow] = roundMoney(result.spendingFlows[flow]);
    });
    return result;
  }

  function averageAccountMovement(rows, months) {
    if (!months.length) return null;
    var totals = {};
    months.forEach(function (month) { totals[month] = 0; });
    rows.forEach(function (transaction) {
      if (totals[transaction.month] === undefined) return;
      totals[transaction.month] += transaction.direction === "deposit"
        ? transaction.amount : -transaction.amount;
    });
    return roundMoney(months.reduce(function (sum, month) {
      return sum + totals[month];
    }, 0) / months.length);
  }

  function averageAccountSpending(rows, months) {
    if (!months.length) return null;
    var totals = {};
    months.forEach(function (month) { totals[month] = 0; });
    rows.forEach(function (transaction) {
      if (totals[transaction.month] === undefined ||
          transaction.direction !== "withdrawal" ||
          accountIsInternalMovement(transaction)) return;
      totals[transaction.month] += transaction.amount;
    });
    return roundMoney(months.reduce(function (sum, month) {
      return sum + totals[month];
    }, 0) / months.length);
  }

  return {
    accountCounterparty: accountCounterparty,
    accountIsInternalMovement: accountIsInternalMovement,
    accountReviewWhitelisted: accountReviewWhitelisted,
    accountSourceOrder: accountSourceOrder,
    analyzeAccountTransactions: analyzeAccountTransactions,
    averageAccountMovement: averageAccountMovement,
    averageAccountSpending: averageAccountSpending,
    averageForMonths: averageForMonths,
    bindToggle: bindToggle,
    groupCountLabel: groupCountLabel,
    groupPurchases: groupPurchases,
    groupAccountTransactions: groupAccountTransactions,
    merchantKey: merchantKey,
    monthLabel: monthLabel,
    settlementPosition: settlementPosition,
    summarize: summarize,
    summarizeAccount: summarizeAccount
  };
});
