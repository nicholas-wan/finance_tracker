"use strict";

var assert = require("node:assert/strict");
var test = require("node:test");
var grouping = require("../app/js/transaction-grouping.js");

function transaction(overrides) {
  return Object.assign({
    description: "GRAB* GPC-AB1234",
    category: "Transport",
    owner: "Shared",
    date: "2026-07-01",
    month: "2026-07",
    type: "debit",
    amount: 10
  }, overrides || {});
}

test("groups normalized descriptions and totals their amount", function () {
  var result = grouping.groupPurchases([
    transaction({ description: "GRAB* GPC-AB1234", amount: 10 }),
    transaction({ description: "GRAB* GPC-ZZ9876", date: "2026-07-03", amount: 12.5 })
  ]);
  assert.equal(result.length, 1);
  assert.equal(result[0].purchaseCount, 2);
  assert.equal(result[0].amount, 22.5);
  assert.equal(result[0].lastDate, "2026-07-03");
});

test("canonicalizes Grab references and variants into one merchant", function () {
  var rows = [
    transaction({ description: "Grab* GPC-71541451439149cSINGAPORE" }),
    transaction({ description: "Grab* ab2d5d37108809ef Singapore" }),
    transaction({ description: "GRAB RIDES-EC PETALING JAYA" }),
    transaction({ description: "GRAB-EC PETALING JAYA" })
  ];
  var result = grouping.groupPurchases(rows);
  assert.equal(result.length, 1);
  assert.equal(result[0].label, "Grab");
  assert.equal(result[0].count, 4);
});

test("canonicalizes NTUC app online and outlet variants", function () {
  var rows = [
    transaction({ description: "NTUC FairPrice App Pay SINGAPORE", category: "Groceries" }),
    transaction({ description: "NTUC FairPrice Online SINGAPORE", category: "Groceries" }),
    transaction({ description: "NTUC FP-YISHUN MRT SINGAPORE", category: "Groceries" }),
    transaction({ description: "NTUC FP - PSA SINGAPORE", category: "Groceries" })
  ];
  var result = grouping.groupPurchases(rows);
  assert.equal(result.length, 1);
  assert.equal(result[0].label, "NTUC FairPrice");
  assert.equal(result[0].count, 4);
});

test("keeps Grab groups separate by category and owner", function () {
  var result = grouping.groupPurchases([
    transaction({ description: "Grab* GPC-ABC12345", category: "Transport", owner: "Shared" }),
    transaction({ description: "Grab Singapore", category: "Food & dining", owner: "Shared" }),
    transaction({ description: "Grab* 8403906325810981 Singapore", category: "Transport", owner: "Nic" })
  ]);
  assert.equal(result.length, 3);
});

test("nets refunds into the grouped amount", function () {
  var result = grouping.groupPurchases([
    transaction({ amount: 25 }),
    transaction({ type: "refund", amount: 5 })
  ]);
  assert.equal(result[0].purchaseCount, 1);
  assert.equal(result[0].refundCount, 1);
  assert.equal(result[0].amount, 20);
});

test("does not merge purchases across category or owner", function () {
  var result = grouping.groupPurchases([
    transaction(),
    transaction({ category: "Food & dining" }),
    transaction({ owner: "Nic" })
  ]);
  assert.equal(result.length, 3);
});

test("summary excludes payments and rebates and nets refunds", function () {
  var result = grouping.summarize([
    transaction({ category: "Food & dining", amount: 20 }),
    transaction({ category: "Food & dining", type: "refund", amount: 5 }),
    transaction({ category: "Payment", type: "payment", amount: 100 })
  ], { Payment: true, Rebates: true });
  assert.equal(result.count, 2);
  assert.equal(result.netCost, 15);
  assert.equal(result.categoryTotals["Food & dining"], 15);
  assert.equal(result.ownerTotals.Shared, 15);
});

test("monthly average includes zero-spend months and nets refunds", function () {
  var result = grouping.averageForMonths([
    transaction({ month: "2026-05", amount: 30 }),
    transaction({ month: "2026-05", type: "refund", amount: 6 }),
    transaction({ month: "2026-06", category: "Payment", type: "payment", amount: 50 })
  ], ["2026-05", "2026-06", "2026-07"], { Payment: true, Rebates: true });
  assert.equal(result, 8);
});

// ---------- Shared settlement position ----------
// Synthetic amounts only. The shape mirrors manual/settlements.json: one
// opening balance keyed by "from", plus a payments list.

function settlementFixture() {
  return {
    openingBalances: [
      { from: "2026-01", youOweYx: 1000, note: "Confirmed at the start of 2026" }
    ],
    payments: []
  };
}

function settlementRows() {
  return [
    transaction({ month: "2026-01", date: "2026-01-10", owner: "Shared", amount: 100 }),
    transaction({ month: "2026-01", date: "2026-01-20", owner: "Yx", amount: 25 }),
    transaction({ month: "2026-01", date: "2026-01-21", owner: "Nic", amount: 40 }),
    transaction({ month: "2026-02", date: "2026-02-05", owner: "Shared", amount: 51 }),
    transaction({ month: "2026-03", date: "2026-03-05", owner: "Shared", amount: 200 }),
    transaction({ month: "2026-03", date: "2026-03-06", owner: "Yx", amount: 10 }),
    // Excluded categories never enter the settlement.
    transaction({ month: "2026-03", date: "2026-03-07", owner: "Shared",
      category: "Payment", type: "payment", amount: 900 })
  ];
}

test("settlement position matches the per-month cent split for the opening year", function () {
  var result = grouping.settlementPosition(settlementRows(), settlementFixture(), "2026", null);
  // 100/2 = 50, 51/2 rounds to 25.50, 200/2 = 100 -> 175.50 shared half.
  assert.equal(result.sharedTotal, 351);
  assert.equal(result.sharedHalf, 175.5);
  assert.equal(result.yxDirect, 35);
  assert.equal(result.newYxShare, 210.5);
  assert.equal(result.hasOpening, true);
  assert.equal(result.carried, false);
  assert.equal(result.openingYouOwe, 1000);
  assert.equal(result.netPosition, -789.5);
  assert.equal(result.youOweYx, 789.5);
  assert.equal(result.scopeLabel, "full year 2026 to date");
  assert.deepEqual(result.months, ["2026-01", "2026-02", "2026-03"]);
  assert.equal(result.ownerTotals.Nic, 40);
});

test("settlement position reproduces the pre-carry-forward calculation for the opening year", function () {
  // Replica of the previous per-page logic: an opening matched only on
  // "<year>-01", whole-year activity, whole-year payments.
  function legacyPosition(rows, settlements, year) {
    var excluded = { Payment: true, Rebates: true };
    var byMonth = {};
    rows.forEach(function (row) {
      if (excluded[row.category] || row.month.slice(0, 4) !== year) return;
      if (!byMonth[row.month]) byMonth[row.month] = { Shared: 0, Yx: 0 };
      if (byMonth[row.month][row.owner] === undefined) return;
      byMonth[row.month][row.owner] += row.type === "debit" ? row.amount : -row.amount;
    });
    var share = 0;
    Object.keys(byMonth).forEach(function (month) {
      var half = Math.round((byMonth[month].Shared / 2) * 100 + 1e-9) / 100;
      share += half + byMonth[month].Yx;
    });
    share = Math.round(share * 100 + 1e-9) / 100;
    var opening = (settlements.openingBalances || []).filter(function (item) {
      return item.from === year + "-01";
    })[0];
    var paid = 0, received = 0;
    (settlements.payments || []).forEach(function (payment) {
      if (!payment.date || payment.date.slice(0, 4) !== year) return;
      if (payment.direction === "toYx") paid += payment.amount;
      else if (payment.direction === "fromYx") received += payment.amount;
    });
    return Math.round((share + paid - received -
      (opening ? opening.youOweYx : 0)) * 100 + 1e-9) / 100;
  }

  var settlements = settlementFixture();
  settlements.payments = [
    { date: "2026-04-01", direction: "toYx", amount: 200 },
    { date: "2026-05-01", direction: "fromYx", amount: 12.5 }
  ];
  var rows = settlementRows();
  var result = grouping.settlementPosition(rows, settlements, "2026", null);
  assert.equal(result.netPosition, legacyPosition(rows, settlements, "2026"));
  assert.equal(result.netPosition, -602);
});

test("settlement position carries a prior year forward when the year has no opening entry", function () {
  var rows = settlementRows();
  var result = grouping.settlementPosition(rows, settlementFixture(), "2027", null);
  var previous = grouping.settlementPosition(rows, settlementFixture(), "2026", null);
  assert.equal(result.hasOpening, true);
  assert.equal(result.carried, true);
  assert.equal(result.carriedFromYear, "2026");
  assert.equal(result.openingFrom, "2026-01");
  // The 2026 closing position becomes the 2027 opening position.
  assert.equal(result.openingYouOwe, previous.youOweYx);
  assert.equal(result.openingYouOwe, 789.5);
  assert.equal(result.newYxShare, 0);
  assert.equal(result.netPosition, -789.5);
  assert.equal(result.youOweYx, 789.5);
  assert.notEqual(result.youOweYx, 0);
});

test("settlement carry forward spans several empty years and counts payments", function () {
  var settlements = settlementFixture();
  settlements.payments = [
    { date: "2026-04-01", direction: "toYx", amount: 200 },
    { date: "2027-06-01", direction: "fromYx", amount: 50 }
  ];
  var result = grouping.settlementPosition(settlementRows(), settlements, "2028", null);
  // 1000 opening − 210.50 share − 200 paid in 2026, then +50 received in 2027.
  assert.equal(result.openingYouOwe, 639.5);
  assert.equal(result.carriedFromYear, "2027");
  assert.equal(result.youOweYx, 639.5);
  assert.equal(result.paidToYx, 0);
  assert.equal(result.receivedFromYx, 0);
});

test("throughMonth excludes later transactions and later payments", function () {
  var settlements = settlementFixture();
  settlements.payments = [
    { date: "2026-02-14", direction: "toYx", amount: 30 },
    { date: "2026-05-14", direction: "toYx", amount: 400 }
  ];
  var result = grouping.settlementPosition(settlementRows(), settlements, "2026", "2026-02");
  assert.deepEqual(result.months, ["2026-01", "2026-02"]);
  assert.equal(result.newYxShare, 100.5);
  assert.equal(result.paidToYx, 30);
  assert.equal(result.paidToYxCount, 1);
  assert.equal(result.receivedFromYx, 0);
  assert.equal(result.netPosition, -869.5);
  assert.equal(result.scopeLabel, "through Feb 2026");
  var full = grouping.settlementPosition(settlementRows(), settlements, "2026", null);
  assert.equal(full.scopeLabel, "full year 2026 to date");
  assert.notEqual(full.netPosition, result.netPosition);
});

test("throughMonth on the last month of scope equals the unscoped year", function () {
  var rows = settlementRows();
  var cutoff = grouping.settlementPosition(rows, settlementFixture(), "2026", "2026-12");
  var full = grouping.settlementPosition(rows, settlementFixture(), "2026", null);
  assert.equal(cutoff.netPosition, full.netPosition);
  assert.equal(cutoff.newYxShare, full.newYxShare);
});

test("an empty openings list reports an unknown position instead of a zero balance", function () {
  var rows = settlementRows();
  var empty = grouping.settlementPosition(rows, { openingBalances: [], payments: [] }, "2027", null);
  assert.equal(empty.hasOpening, false);
  assert.equal(empty.carried, false);
  assert.equal(empty.openingFrom, null);
  assert.equal(empty.openingYouOwe, 0);
  assert.equal(empty.newYxShare, 0);
  // With no opening recorded the caller must not present this as a balance.
  assert.equal(empty.netPosition, 0);
  var known = grouping.settlementPosition(rows, settlementFixture(), "2027", null);
  assert.equal(known.hasOpening, true);
  assert.notEqual(known.youOweYx, 0);
});

test("an opening recorded after the displayed year is not applied to it", function () {
  var result = grouping.settlementPosition(settlementRows(), settlementFixture(), "2025", null);
  assert.equal(result.hasOpening, false);
  assert.equal(result.openingYouOwe, 0);
  assert.equal(result.newYxShare, 0);
});

test("a same-year opening recorded after the cutoff month is not applied", function () {
  var settlements = {
    openingBalances: [
      { from: "2026-01", youOweYx: 1000 },
      { from: "2026-06", youOweYx: 5000 }
    ],
    payments: []
  };
  // Scoped through March, the June opening does not exist yet: the January
  // record applies. Unscoped, the June record is the most recent one.
  var scoped = grouping.settlementPosition(settlementRows(), settlements, "2026", "2026-03");
  assert.equal(scoped.openingFrom, "2026-01");
  assert.equal(scoped.openingYouOwe, 1000);
  var unscoped = grouping.settlementPosition(settlementRows(), settlements, "2026", null);
  assert.equal(unscoped.openingFrom, "2026-06");
  assert.equal(unscoped.openingYouOwe, 5000);
  // With only the June record, a view that stops in March has no opening at
  // all rather than borrowing one from the future.
  var onlyFuture = grouping.settlementPosition(
    settlementRows(),
    { openingBalances: [{ from: "2026-06", youOweYx: 5000 }], payments: [] },
    "2026",
    "2026-03"
  );
  assert.equal(onlyFuture.hasOpening, false);
  assert.equal(onlyFuture.openingYouOwe, 0);
});

test("an opening recorded mid-year ignores the months already inside it", function () {
  var settlements = {
    openingBalances: [{ from: "2026-03", youOweYx: 500 }],
    payments: []
  };
  var result = grouping.settlementPosition(settlementRows(), settlements, "2026", null);
  assert.equal(result.startMonth, "2026-03");
  assert.deepEqual(result.months, ["2026-03"]);
  assert.equal(result.newYxShare, 110);
  assert.equal(result.youOweYx, 390);
});

test("account summary keeps deposits withdrawals and balance distinct", function () {
  var result = grouping.summarizeAccount([
    { date: "2026-07-01", direction: "deposit", amount: 100, flow: "Salary", balance: 100 },
    { date: "2026-07-02", direction: "withdrawal", amount: 30, flow: "Cash & NETS", balance: 70 },
    { date: "2026-07-03", direction: "withdrawal", amount: 10, flow: "Cash & NETS", balance: 60 }
  ]);
  assert.equal(result.deposits, 100);
  assert.equal(result.withdrawals, 40);
  assert.equal(result.netMovement, 60);
  assert.equal(result.withdrawalFlows["Cash & NETS"], 40);
  assert.deepEqual(result.latestBalance, { date: "2026-07-03", amount: 60 });
});

test("account movement average includes months with no matching rows", function () {
  var result = grouping.averageAccountMovement([
    { month: "2026-05", direction: "deposit", amount: 90 },
    { month: "2026-05", direction: "withdrawal", amount: 30 },
    { month: "2026-06", direction: "withdrawal", amount: 30 }
  ], ["2026-05", "2026-06", "2026-07"]);
  assert.equal(result, 10);
});

test("every bank statement month produces an exact account movement summary", function () {
  var account = require("../app/data/account_transactions.json");
  account.months.forEach(function (month) {
    var rows = account.transactions.filter(function (row) { return row.month === month; });
    var summary = grouping.summarizeAccount(rows);
    var expected = Math.round(rows.reduce(function (total, row) {
      return total + (row.direction === "deposit" ? row.amount : -row.amount);
    }, 0) * 100) / 100;
    assert.equal(summary.count, rows.length);
    assert.equal(summary.netMovement, expected, month + " account movement should reconcile");
  });
});

test("toggle updates its visible state and invokes the page callback", function () {
  var listeners = {};
  var classes = new Set();
  var attributes = {};
  var button = {
    addEventListener: function (name, callback) { listeners[name] = callback; },
    classList: {
      toggle: function (name, enabled) {
        if (enabled) classes.add(name);
        else classes.delete(name);
      }
    },
    setAttribute: function (name, value) { attributes[name] = value; }
  };
  var label = { textContent: "" };
  var states = [];
  grouping.bindToggle(button, label, function (active) { states.push(active); }, false);
  assert.equal(attributes["aria-pressed"], "false");
  assert.equal(label.textContent, "Group purchases");
  listeners.click();
  assert.equal(attributes["aria-pressed"], "true");
  assert.equal(label.textContent, "Show individual");
  assert.equal(classes.has("active"), true);
  assert.deepEqual(states, [true]);
});

test("every statement month and all-time conserve net cost when grouped", function () {
  var data = require("../app/data/transactions.json");
  var periods = data.months.concat(["all"]);
  periods.forEach(function (period) {
    var periodRows = data.transactions.filter(function (row) {
      return period === "all" || row.month === period;
    });
    var costRows = periodRows.filter(function (row) {
      return row.category !== "Payment" && row.category !== "Rebates";
    });
    var summary = grouping.summarize(periodRows, { Payment: true, Rebates: true });
    var expectedCost = Math.round(costRows.reduce(function (total, row) {
      return total + (row.type === "debit" ? row.amount : -row.amount);
    }, 0) * 100) / 100;
    assert.equal(summary.count, costRows.length, period + " summary should include every cost row");
    assert.equal(summary.netCost, expectedCost, period + " summary total should match cost rows");
    var views = [
      periodRows,
      costRows
    ];
    views.forEach(function (rows) {
      assert.ok(rows.length > 0, period + " should have transactions");
      var groups = grouping.groupPurchases(rows);
      var rowNet = Math.round(rows.reduce(function (total, row) {
        return total + (row.type === "debit" ? row.amount : -row.amount);
      }, 0) * 100) / 100;
      var groupNet = Math.round(groups.reduce(function (total, group) {
        return total + group.amount;
      }, 0) * 100) / 100;
      assert.equal(groupNet, rowNet, period + " grouped total should match its rows");
    });
  });
});
