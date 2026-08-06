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
    return key.trim().replace(/\s+/g, " ") || String(description || "").toUpperCase().trim();
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

  function groupPurchases(rows) {
    var groups = {};
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
          amount: 0,
          riskCount: 0
        };
      }
      var group = groups[key];
      group.count += 1;
      group.amount += signed(transaction);
      if (transaction.type === "debit") group.purchaseCount += 1;
      else if (transaction.type === "refund") group.refundCount += 1;
      if (transaction.risk && !transaction.risk.recognized) group.riskCount += 1;
      if ((transaction.date || transaction.month) > group.lastDate) {
        group.lastDate = transaction.date || transaction.month;
      }
      if (transaction.displayName) group.label = transaction.displayName;
    });
    return Object.keys(groups).map(function (key) {
      groups[key].amount = roundMoney(groups[key].amount);
      return groups[key];
    }).sort(function (left, right) {
      return Math.abs(right.amount) - Math.abs(left.amount) ||
        right.lastDate.localeCompare(left.lastDate);
    });
  }

  function summarize(rows, excludedCategories) {
    var result = {
      count: 0,
      netCost: 0,
      categoryTotals: {},
      ownerTotals: {}
    };
    rows.forEach(function (transaction) {
      if (excludedCategories[transaction.category]) return;
      var value = signed(transaction);
      result.count += 1;
      result.netCost += value;
      result.categoryTotals[transaction.category] =
        (result.categoryTotals[transaction.category] || 0) + value;
      result.ownerTotals[transaction.owner] =
        (result.ownerTotals[transaction.owner] || 0) + value;
    });
    result.netCost = roundMoney(result.netCost);
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
    // before the displayed year, not only one recorded in January of it.
    var opening = null;
    openings.forEach(function (item) {
      if (!item || !item.from) return;
      if (String(item.from).slice(0, 4) > displayYear) return;
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
      withdrawalFlows: {},
      latestBalance: null
    };
    rows.forEach(function (transaction) {
      if (transaction.direction === "deposit") result.deposits += transaction.amount;
      else result.withdrawals += transaction.amount;
      if (transaction.direction === "withdrawal") {
        result.withdrawalFlows[transaction.flow] =
          (result.withdrawalFlows[transaction.flow] || 0) + transaction.amount;
      }
      if (transaction.balance !== undefined && transaction.balance !== null &&
          (!result.latestBalance || transaction.date >= result.latestBalance.date)) {
        result.latestBalance = { date: transaction.date, amount: transaction.balance };
      }
    });
    result.deposits = roundMoney(result.deposits);
    result.withdrawals = roundMoney(result.withdrawals);
    result.netMovement = roundMoney(result.deposits - result.withdrawals);
    Object.keys(result.withdrawalFlows).forEach(function (flow) {
      result.withdrawalFlows[flow] = roundMoney(result.withdrawalFlows[flow]);
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

  return {
    averageAccountMovement: averageAccountMovement,
    averageForMonths: averageForMonths,
    bindToggle: bindToggle,
    groupPurchases: groupPurchases,
    merchantKey: merchantKey,
    monthLabel: monthLabel,
    settlementPosition: settlementPosition,
    summarize: summarize,
    summarizeAccount: summarizeAccount
  };
});
