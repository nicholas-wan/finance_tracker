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
    return transaction.displayName ||
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
      var k = window.FinanceGrouping.merchantKey(t.description);
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
      if (t.displayName) countName(byMerchant[k].displayNames, t.displayName);
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
  return { build: build, money: money, monthLabel: label };
})();
