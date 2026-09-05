"use strict";

var assert = require("node:assert/strict");
var test = require("node:test");
var grouping = require("../app/js/transaction-grouping.js");

// Identity is runtime configuration loaded from the generated data file, so
// the tests supply their own placeholders and never carry real names or
// account numbers.
var PLACEHOLDER_IDENTITY = {
  knownAccounts: {
    "1111111111": "Own Savings A/c",
    "2222222222": "Own Stash A/c (closed)"
  },
  trustedCounterparties: [
    "Partner Full Name",
    "Yx",
    "Nickname",
    "Own Savings A/c",
    "Own Stash A/c (closed)",
    // Only a starred entry matches beyond its exact text, and only from eight
    // characters up, so a statement that cuts a firm name short still lands on
    // the same payee.
    "Trusted Vendor*"
  ]
};

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
  assert.equal(result.openingBalance, 0);
  assert.equal(result.closingBalance, 60);
  assert.equal(result.reconciliationGap, 0);
  assert.equal(result.latestBalance.date, "2026-07-03");
  assert.equal(result.latestBalance.amount, 60);
});

test("account closing balance follows statement order when dates match", function () {
  var result = grouping.summarizeAccount([
    { month: "2026-07", date: "2026-07-31", direction: "deposit", amount: 3.39,
      flow: "Interest", balance: 82916.10,
      provenance: { sourceFile: "JUL.pdf", page: 4, line: 59 } },
    { month: "2026-07", date: "2026-07-31", direction: "withdrawal", amount: 1.40,
      flow: "Transfer", balance: 82912.71,
      provenance: { sourceFile: "JUL.pdf", page: 4, line: 54 } }
  ]);
  assert.equal(result.closingBalance, 82916.10);
  assert.equal(result.latestBalance.amount, 82916.10);
  assert.equal(result.reconciliationGap, 0);
});

test("bank counterparty cleanup groups references without merging flows", function () {
  assert.equal(grouping.accountCounterparty(
    "Inward Debit-FAST OTHR U7869181.615725 Interactive Brokers U7869181.2147898"),
  "Interactive Brokers");
  assert.equal(grouping.accountCounterparty(
    "NETS Debit-Consumer DSTA DRINKS10142900 xxxxxx5080"), "DSTA Drinks");
  var groups = grouping.groupAccountTransactions([
    { description: "NETS Debit-Consumer DSTA DRINKS10142900 xxxxxx5080",
      flow: "Cash & NETS", direction: "withdrawal", amount: 5, date: "2026-07-01" },
    { description: "NETS Debit-Consumer DSTA DRINKS10142901 xxxxxx5080",
      flow: "Cash & NETS", direction: "withdrawal", amount: 7, date: "2026-07-02" }
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].label, "DSTA Drinks");
  assert.equal(groups[0].amount, 12);
  assert.equal(groups[0].count, 2);
});

test("bank review analysis flags conservative explainable cases", function () {
  var rows = [
    { id: "a", month: "2026-07", date: "2026-07-01", description: "PAYNOW-FAST SOMEONE",
      flow: "Transfer", direction: "withdrawal", amount: 800,
      provenance: { sourceFile: "JUL.pdf", page: 1, line: 1, verified: true } },
    { id: "b", month: "2026-07", date: "2026-07-02", description: "UNKNOWN CREDIT",
      flow: "Other", direction: "deposit", amount: 10,
      provenance: { sourceFile: "JUL.pdf", page: 1, line: 2, verified: true } },
    { id: "c", month: "2026-07", date: "2026-07-03",
      description: "Misc Credit ONE TAX PROMO 2602", flow: "Other",
      direction: "deposit", amount: 10,
      provenance: { sourceFile: "JUL.pdf", page: 1, line: 3, verified: true } }
  ];
  var result = grouping.analyzeAccountTransactions(rows, { a: ["large-transfer"] });
  assert.equal(result.a.requiresReview, true);
  assert.equal(result.a.reviewed, true);
  assert.ok(result.a.checks.includes("large-transfer"));
  assert.ok(result.b.checks.includes("unclassified"));
  assert.equal(result.c.requiresReview, false);
  assert.deepEqual(result.c.checks, []);
  assert.equal(grouping.accountReviewWhitelisted({
    description: "Misc Credit", direction: "deposit"
  }), true);
  assert.equal(grouping.accountReviewWhitelisted({
    description: "UNKNOWN CREDIT", direction: "deposit"
  }), false);
});

test("trusted counterparties skip amount checks but keep integrity checks", function () {
  grouping.configure(PLACEHOLDER_IDENTITY);
  function row(id, description, amount, extra) {
    return Object.assign({
      id: id, month: "2026-07", date: "2026-07-0" + id.length,
      description: description, flow: "Transfer", direction: "withdrawal",
      amount: amount,
      provenance: { sourceFile: "JUL.pdf", page: 1, line: id.length, verified: true }
    }, extra || {});
  }
  var rows = [
    row("a", "PAYNOW-FAST PARTNER FULL NAME OTHR Transfer - Mobile", 2000),
    // A truncated trailing word still matches, because the trusted entry is
    // starred and its stem is long enough to opt into prefix matching.
    row("ab", "PAYNOW-FAST TRUSTED VENDOR PTE. LT OTHR Transfer - UEN", 9000),
    row("abc", "PAYNOW-FAST A STRANGER OTHR Transfer - Mobile", 2000)
  ];
  var result = grouping.analyzeAccountTransactions(rows, {});
  assert.equal(result.a.requiresReview, false);
  assert.equal(result.ab.requiresReview, false);
  assert.ok(result.abc.checks.includes("large-transfer"));
  // Trust does not extend to data quality: an unreconciled source still flags.
  var unverified = grouping.analyzeAccountTransactions([
    row("a", "PAYNOW-FAST PARTNER FULL NAME OTHR Transfer - Mobile", 2000,
      { provenance: { sourceFile: "JUL.pdf", page: 1, line: 1, verified: false } })
  ], {});
  assert.ok(unverified.a.checks.includes("unverified-source"));
  grouping.configure({});
});

test("without configuration nobody is trusted and every mBK number is shown raw", function () {
  grouping.configure();
  assert.deepEqual(grouping.getIdentity(),
    { knownAccounts: {}, trustedCounterparties: [] });
  assert.equal(grouping.accountCounterparty("Funds Transfer mBK-1111111111"),
    "UOB account 111-111-111-1");
  var result = grouping.analyzeAccountTransactions([{
    id: "n1", month: "2026-07", date: "2026-07-11",
    description: "PAYNOW-FAST PARTNER FULL NAME OTHR Transfer - Mobile",
    flow: "Transfer", direction: "withdrawal", amount: 2000,
    provenance: { sourceFile: "JUL.pdf", page: 1, line: 1, verified: true }
  }], {});
  assert.ok(result.n1.checks.includes("large-transfer"));
  assert.equal(result.n1.requiresReview, true);
});

test("getIdentity returns the configured values and not the live state", function () {
  grouping.configure(PLACEHOLDER_IDENTITY);
  var identity = grouping.getIdentity();
  assert.deepEqual(identity.knownAccounts, PLACEHOLDER_IDENTITY.knownAccounts);
  assert.deepEqual(identity.trustedCounterparties,
    PLACEHOLDER_IDENTITY.trustedCounterparties);
  // A caller that renders the list must not be able to edit the module state.
  identity.trustedCounterparties.push("Intruder");
  identity.knownAccounts["3333333333"] = "Injected A/c";
  assert.deepEqual(grouping.getIdentity().trustedCounterparties,
    PLACEHOLDER_IDENTITY.trustedCounterparties);
  assert.equal(grouping.accountCounterparty("Funds Transfer mBK-3333333333"),
    "UOB account 333-333-333-3");
  // A partial identity keeps the missing half empty rather than stale.
  grouping.configure({ trustedCounterparties: ["Someone"] });
  assert.deepEqual(grouping.getIdentity(),
    { knownAccounts: {}, trustedCounterparties: ["Someone"] });
  grouping.configure({});
});

test("outflows under S$50 never require review, whatever the checks say", function () {
  function rows(count, amount) {
    var list = [];
    for (var index = 0; index < count; index += 1) {
      list.push({
        id: "s" + index, month: "2026-07", date: "2026-07-05",
        description: "PAYNOW-FAST DINNER FRIEND OTHR Transfer - Mobile",
        flow: "Transfer", direction: "withdrawal", amount: amount,
        provenance: { sourceFile: "JUL.pdf", page: 1, line: index + 1, verified: true }
      });
    }
    return list;
  }
  // Repeated identical payments are a possible double charge and still
  // demand review even below the small-outflow floor.
  var burst = grouping.analyzeAccountTransactions(rows(4, 25), {});
  assert.ok(burst.s0.checks.includes("possible-duplicate"));
  assert.equal(burst.s0.requiresReview, true);
  var large = grouping.analyzeAccountTransactions(rows(2, 60), {});
  assert.ok(large.s0.checks.includes("possible-duplicate"));
  assert.equal(large.s0.requiresReview, true);
  // A small unclassified outflow also stays out of the queue.
  var unclassified = grouping.analyzeAccountTransactions([{
    id: "u1", month: "2026-07", date: "2026-07-06",
    description: "NETS Debit-Consumer NOODLE STALL10142900 xxxxxx5080",
    flow: "Other", direction: "withdrawal", amount: 6.5,
    provenance: { sourceFile: "JUL.pdf", page: 1, line: 1, verified: true }
  }], {});
  assert.ok(unclassified.u1.checks.includes("unclassified"));
  assert.equal(unclassified.u1.requiresReview, false);
});

test("mBK references resolve to account names or formatted numbers", function () {
  grouping.configure(PLACEHOLDER_IDENTITY);
  assert.equal(grouping.accountCounterparty("Funds Transfer mBK-1111111111"),
    "Own Savings A/c");
  assert.equal(grouping.accountCounterparty("Funds Transfer mBK-2222222222"),
    "Own Stash A/c (closed)");
  assert.equal(grouping.accountCounterparty("Funds Transfer mBK-1234567890"),
    "UOB account 123-456-789-0");
  // Card bill payments carry mBK- followed by text, not an account number.
  assert.equal(grouping.accountCounterparty(
    "Bill Payment mBK-UOB Cards 5522532030690754"), "UOB Cards");
  // A transfer to a known own account is trusted; an unknown one still flags.
  function transfer(id, reference) {
    return {
      id: id, month: "2026-07", date: "2026-07-10",
      description: "Funds Transfer mBK-" + reference,
      flow: "Transfer", direction: "withdrawal", amount: 10000,
      provenance: { sourceFile: "JUL.pdf", page: 1, line: 1, verified: true }
    };
  }
  var known = grouping.analyzeAccountTransactions([transfer("k1", "1111111111")], {});
  assert.equal(known.k1.requiresReview, false);
  var unknown = grouping.analyzeAccountTransactions([transfer("u1", "1234567890")], {});
  assert.ok(unknown.u1.checks.includes("large-transfer"));
  grouping.configure({});
});

// Trust matching is a security boundary: an entry short enough to be a bare
// first name or a nickname must never widen to a counterparty that merely
// starts with it, or a large transfer to a stranger leaves the review queue.
function trustProbe(description, amount) {
  var result = grouping.analyzeAccountTransactions([{
    id: "t1", month: "2026-07", date: "2026-07-15",
    description: description,
    flow: "Transfer", direction: "withdrawal",
    amount: amount === undefined ? 800 : amount,
    provenance: { sourceFile: "JUL.pdf", page: 1, line: 1, verified: true }
  }], {});
  return result.t1;
}

test("a configured nickname is a trusted counterparty", function () {
  grouping.configure(PLACEHOLDER_IDENTITY);
  var probe = trustProbe(
    "PAYNOW-FAST PIB2503305595567963 Nickname OTHR Transfer - Mobile");
  assert.equal(probe.counterparty, "Nickname");
  assert.equal(probe.requiresReview, false);
  grouping.configure({});
});

test("trusted names match exactly and case-insensitively", function () {
  grouping.configure({ trustedCounterparties: ["pArTnEr FuLl NaMe"] });
  assert.equal(trustProbe(
    "PAYNOW-FAST PARTNER FULL NAME OTHR Transfer - Mobile").requiresReview,
  false);
  // The counterparty side is normalized too: repeated spaces in the entry are
  // collapsed rather than making it unmatchable.
  grouping.configure({ trustedCounterparties: ["  Partner   Full  Name  "] });
  assert.deepEqual(grouping.getIdentity().trustedCounterparties,
    ["Partner Full Name"]);
  assert.equal(trustProbe(
    "PAYNOW-FAST PARTNER FULL NAME OTHR Transfer - Mobile").requiresReview,
  false);
  grouping.configure({});
});

test("a bare first-name entry does not trust a stranger who shares it",
  function () {
    grouping.configure(PLACEHOLDER_IDENTITY);
    var probe = trustProbe(
      "PAYNOW-FAST PIB2503305595567963 Nickname OTHR Othername", 9000);
    assert.equal(probe.counterparty, "Nickname Othername");
    assert.ok(probe.checks.includes("large-transfer"));
    assert.equal(probe.requiresReview, true);
    // A stem under eight characters cannot buy its way out with a star: the
    // star is ignored and the entry stays exact-only.
    grouping.configure({ trustedCounterparties: ["Nick*"] });
    assert.ok(trustProbe(
      "PAYNOW-FAST NICK OTHERNAME OTHR Transfer - Mobile", 9000)
      .checks.includes("large-transfer"));
    // The stem itself is still trusted, exactly.
    assert.equal(trustProbe(
      "PAYNOW-FAST NICK OTHR Transfer - Mobile", 9000).requiresReview, false);
    grouping.configure({});
  });

test("a starred entry of eight or more characters matches at a word boundary",
  function () {
    grouping.configure({ trustedCounterparties: ["Trusted Vendor*"] });
    // The statement cuts the firm name at a different point each month.
    assert.equal(trustProbe(
      "PAYNOW-FAST TRUSTED VENDOR PTE L OTHR Transfer - UEN", 9000)
      .requiresReview, false);
    assert.equal(trustProbe(
      "PAYNOW-FAST TRUSTED VENDOR OTHR Transfer - UEN", 9000).requiresReview,
    false);
    // Mid-word continuation is a different party and stays in the queue.
    var other = trustProbe(
      "PAYNOW-FAST TRUSTED VENDORX LTD OTHR Transfer - UEN", 9000);
    assert.equal(other.counterparty, "Trusted Vendorx Ltd");
    assert.ok(other.checks.includes("large-transfer"));
    grouping.configure({});
  });

test("blank and star-only trusted entries are dropped", function () {
  grouping.configure({ trustedCounterparties: ["", "   ", "*", " * ", "Yx"] });
  assert.deepEqual(grouping.getIdentity().trustedCounterparties, ["Yx"]);
  // Nothing was left that could match every counterparty.
  assert.ok(trustProbe("PAYNOW-FAST A STRANGER OTHR Transfer - Mobile", 9000)
    .checks.includes("large-transfer"));
  assert.equal(trustProbe("PAYNOW-FAST YX OTHR Transfer - Mobile", 9000)
    .requiresReview, false);
  grouping.configure({});
});

test("deposits never require review, but keep their data-quality notes", function () {
  var result = grouping.analyzeAccountTransactions([{
    id: "d1", month: "2026-07", date: "2026-07-08",
    description: "UNKNOWN CREDIT", flow: "Other", direction: "deposit",
    amount: 250,
    provenance: { sourceFile: "JUL.pdf", page: 1, line: 1, verified: true }
  }], {});
  assert.ok(result.d1.checks.includes("unclassified"));
  assert.equal(result.d1.requiresReview, false);
});

test("a deposit does not hide the first later payment to the same counterparty", function () {
  var shared = {
    month: "2026-07", flow: "Other", amount: 600,
    provenance: { sourceFile: "JUL.pdf", page: 1, verified: true }
  };
  var result = grouping.analyzeAccountTransactions([
    Object.assign({}, shared, { id: "in", date: "2026-07-01", line: 1,
      description: "PAYNOW-FAST NEW PAYEE OTHR Transfer - Mobile", direction: "deposit" }),
    Object.assign({}, shared, { id: "out", date: "2026-07-02", line: 2,
      description: "PAYNOW-FAST NEW PAYEE OTHR Transfer - Mobile", direction: "withdrawal" })
  ], {});
  assert.ok(result.out.checks.includes("new-counterparty"));
});

test("recognition is invalidated when the checks on a bank row change", function () {
  var row = {
    id: "r1", month: "2026-07", date: "2026-07-15",
    description: "PAYNOW-FAST A STRANGER OTHR Transfer - Mobile",
    flow: "Transfer", direction: "withdrawal", amount: 800,
    provenance: { sourceFile: "JUL.pdf", page: 1, line: 1, verified: true }
  };
  var checks = grouping.analyzeAccountTransactions([row], {}).r1.checks;
  assert.equal(grouping.analyzeAccountTransactions(
    [row], { r1: checks }
  ).r1.reviewed, true);
  assert.equal(grouping.analyzeAccountTransactions(
    [row], { r1: checks.concat(["unverified-source"]) }
  ).r1.reviewed, false);
});

test("incoming transfer duplicates stay quiet, outgoing still flag", function () {
  // Amount sits above the small-outflow allowance so only direction differs.
  function pair(direction) {
    return ["p", "q"].map(function (id) {
      return {
        id: direction + id, month: "2026-07", date: "2026-07-04",
        description: "PAYNOW-FAST SAME PERSON OTHR Transfer - Mobile",
        flow: "Transfer", direction: direction, amount: 75,
        provenance: { sourceFile: "JUL.pdf", page: 1,
          line: id === "p" ? 1 : 2, verified: true }
      };
    });
  }
  var incoming = grouping.analyzeAccountTransactions(pair("deposit"), {});
  assert.equal(incoming.depositp.requiresReview, false);
  assert.equal(incoming.depositq.requiresReview, false);
  var outgoing = grouping.analyzeAccountTransactions(pair("withdrawal"), {});
  assert.ok(outgoing.withdrawalp.checks.includes("possible-duplicate"));
});

test("account spending average excludes internal movements", function () {
  var result = grouping.averageAccountSpending([
    { month: "2026-05", direction: "withdrawal", flow: "Investment", amount: 1000 },
    { month: "2026-05", direction: "withdrawal", flow: "Tax", amount: 100 },
    { month: "2026-06", direction: "withdrawal", flow: "Cash & NETS", amount: 50 }
  ], ["2026-05", "2026-06"]);
  assert.equal(result, 75);
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

// ---------- Merchant key: truncated city suffixes ----------
// A statement line is cut to a fixed width, so the trailing city arrives
// chopped and the same merchant used to split across group keys. Synthetic
// descriptors only.

test("truncated Singapore suffixes collapse into one merchant", function () {
  var rows = [
    transaction({ description: "NORTHFIELD BAKERY SINGAPORE", category: "Food & dining" }),
    transaction({ description: "NORTHFIELD BAKERY SINGAPO", category: "Food & dining" }),
    transaction({ description: "NORTHFIELD BAKERY SINGAP", category: "Food & dining" }),
    transaction({ description: "NORTHFIELD BAKERY SING", category: "Food & dining" }),
    transaction({ description: "NORTHFIELD BAKERY J", category: "Food & dining" })
  ];
  var result = grouping.groupPurchases(rows);
  assert.equal(result.length, 1);
  assert.equal(result[0].count, 5);
  assert.equal(grouping.merchantKey("NORTHFIELD BAKERY SINGAPO"), "NORTHFIELD BAKERY");
});

test("truncated Malaysian city suffixes collapse into one merchant", function () {
  var rows = [
    transaction({ description: "LANTERN GRILL PETALING JAYA" }),
    transaction({ description: "LANTERN GRILL PETALING JAY" }),
    transaction({ description: "LANTERN GRILL PETALIN" }),
    transaction({ description: "LANTERN GRILL JOHOR BAHRU" }),
    transaction({ description: "LANTERN GRILL JOHOR BAHR" }),
    transaction({ description: "LANTERN GRILL JOHOR" })
  ];
  var result = grouping.groupPurchases(rows);
  assert.equal(result.length, 1);
  assert.equal(result[0].count, 6);
});

test("words that merely start like a city are not treated as truncations", function () {
  var rows = [
    transaction({ description: "AURORA SINGTEL", category: "Bills & utilities" }),
    transaction({ description: "AURORA SINGLIFE", category: "Bills & utilities" }),
    transaction({ description: "AURORA SINGAPURA", category: "Bills & utilities" }),
    transaction({ description: "AURORA BAHRU", category: "Bills & utilities" }),
    transaction({ description: "AURORA", category: "Bills & utilities" })
  ];
  assert.equal(grouping.groupPurchases(rows).length, 5);
});

test("stripping a city suffix never empties the merchant key", function () {
  assert.equal(grouping.merchantKey("SINGAPO"), "SINGAPO");
  assert.equal(grouping.merchantKey("J"), "J");
  assert.equal(grouping.merchantKey("HARBOUR DELI J J"), "HARBOUR DELI");
});

test("no live merchant key keeps a truncated city or stray letter tail", function () {
  var data = require("../app/data/transactions.json");
  var keys = {};
  data.transactions.forEach(function (row) {
    var key = grouping.merchantKey(row.description);
    assert.ok(key.length > 0, "a merchant key should never be empty");
    // A descriptor the normalizer consumes entirely falls back to its raw
    // text, which is deliberately left untouched — nothing else is left to
    // group on. Those rows are outside what the suffix trimming governs.
    if (key !== String(row.description).toUpperCase().trim()) keys[key] = true;
  });
  assert.ok(Object.keys(keys).length > 100, "the live file should exercise this");
  ["SINGAPORE", "PETALING", "JOHOR"].forEach(function (city) {
    Object.keys(keys).forEach(function (key) {
      var tokens = key.split(" ");
      if (tokens.length < 2) return;
      var last = tokens[tokens.length - 1];
      assert.notEqual(last.length, 1, "stray letter tail should be trimmed: " + key);
      assert.ok(last.length < 4 || city.indexOf(last) !== 0,
        "truncated city tail should be trimmed: " + key);
    });
  });
});

test("every generated merchant key matches the browser normalizer", function () {
  var data = require("../app/data/transactions.json");
  data.transactions.forEach(function (row) {
    assert.equal(row.merchantKey, grouping.merchantKey(row.description), row.id);
  });
});

// ---------- Grouped row counts ----------

test("a payment group is counted as payments, not zero purchases", function () {
  var groups = grouping.groupPurchases([
    transaction({ description: "CARD PAYMENT THANK YOU", category: "Payment",
      type: "payment", amount: 900 }),
    transaction({ description: "CARD PAYMENT THANK YOU", category: "Payment",
      type: "payment", amount: 400, date: "2026-07-05" })
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].count, 2);
  assert.equal(groups[0].purchaseCount, 0);
  assert.equal(groups[0].paymentCount, 2);
  assert.equal(grouping.groupCountLabel(groups[0]), "2 payments");
});

test("grouped count parts always add up to the group count", function () {
  var groups = grouping.groupPurchases([
    transaction({ amount: 30 }),
    transaction({ type: "refund", amount: 5, date: "2026-07-02" }),
    transaction({ type: "payment", amount: 100, date: "2026-07-03" }),
    transaction({ type: "rebate", amount: 2, date: "2026-07-04" })
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].count, 4);
  assert.equal(groups[0].purchaseCount + groups[0].refundCount +
    groups[0].paymentCount + groups[0].otherCount, groups[0].count);
  assert.equal(grouping.groupCountLabel(groups[0]),
    "1 purchase · 1 refund · 1 payment · 1 row");
});

test("a group with no typed rows falls back to its own count", function () {
  assert.equal(grouping.groupCountLabel({ count: 70 }), "70 rows");
  assert.equal(grouping.groupCountLabel({ count: 1 }), "1 row");
});

test("every live grouped label accounts for all of its rows", function () {
  var data = require("../app/data/transactions.json");
  grouping.groupPurchases(data.transactions).forEach(function (group) {
    assert.equal(group.purchaseCount + group.refundCount +
      group.paymentCount + group.otherCount, group.count);
    assert.ok(!/^0 /.test(grouping.groupCountLabel(group)),
      "a group should never report zero of anything as its whole label");
  });
});

test("a group carries the id of every row it covers, and no others", function () {
  // One click on a group's owner chip retags exactly these ids, so a missing
  // or foreign id would silently skip or mistag a real transaction.
  var rows = [
    transaction({ id: "tx_1", description: "SHOPEE SG MP SINGAPORE", owner: "Nic" }),
    transaction({ id: "tx_2", description: "SHOPEE SG MP SINGAPORE", owner: "Nic" }),
    transaction({ id: "tx_3", description: "SHOPEE SG MP SINGAPORE", owner: "Shared" })
  ];
  var groups = grouping.groupPurchases(rows);
  var seen = [];
  groups.forEach(function (group) {
    assert.equal(group.ids.length, group.count);
    // The grouping key includes the owner, so one group never mixes owners.
    group.ids.forEach(function (id) {
      var row = rows.find(function (r) { return r.id === id; });
      assert.equal(row.owner, group.owner);
      seen.push(id);
    });
  });
  assert.deepEqual(seen.sort(), ["tx_1", "tx_2", "tx_3"]);
});

test("every live group's ids reconcile to its own count", function () {
  var data = require("../app/data/transactions.json");
  var total = 0;
  grouping.groupPurchases(data.transactions).forEach(function (group) {
    assert.equal(group.ids.length, group.count);
    total += group.ids.length;
  });
  assert.equal(total, data.transactions.length);
});

// ---------- Excluded rows are reported, never re-netted ----------

test("summary reports excluded rows separately from net cost", function () {
  var result = grouping.summarize([
    transaction({ category: "Food & dining", amount: 20 }),
    transaction({ category: "Food & dining", type: "refund", amount: 5 }),
    transaction({ category: "Payment", type: "payment", amount: 100 }),
    transaction({ category: "Rebates", type: "rebate", amount: 3 })
  ], { Payment: true, Rebates: true });
  assert.equal(result.count, 2);
  assert.equal(result.netCost, 15);
  assert.equal(result.excludedCount, 2);
  assert.equal(result.excludedTotal, -103);
});

test("net cost plus excluded rows reconciles to every visible row", function () {
  var data = require("../app/data/transactions.json");
  var excluded = { Payment: true, Rebates: true };
  data.months.concat(["all"]).forEach(function (period) {
    var rows = data.transactions.filter(function (row) {
      return period === "all" || row.month === period;
    });
    var totals = grouping.summarize(rows, excluded);
    var everyRow = Math.round(rows.reduce(function (sum, row) {
      return sum + (row.type === "debit" ? row.amount : -row.amount);
    }, 0) * 100) / 100;
    assert.equal(totals.count + totals.excludedCount, rows.length, period);
    assert.equal(Math.round((totals.netCost + totals.excludedTotal) * 100) / 100,
      everyRow, period + " net cost plus excluded rows should cover the view");
  });
});

test("an owner outside the known set still gets a bucket", function () {
  var result = grouping.summarize([
    transaction({ owner: undefined, category: "Food & dining", amount: 10 }),
    transaction({ owner: "", category: "Food & dining", amount: 5 })
  ], {});
  assert.equal(result.ownerTotals.Untagged, 15);
  assert.ok(!Number.isNaN(result.ownerTotals.Untagged));
  assert.equal(Object.keys(result.ownerTotals).length, 1);
});

// ---------- Deterministic grouped label ----------

test("the grouped label is the most frequent name, not the last one written", function () {
  var rows = [
    transaction({ description: "BRIGHTON CAFE SINGAPORE", displayName: "Brighton Cafe" }),
    transaction({ description: "BRIGHTON CAFE SINGAPO", displayName: "Brighton Cafe",
      date: "2026-07-02" }),
    transaction({ description: "BRIGHTON CAFE SING", displayName: "Brighton Cafe (old)",
      date: "2026-07-03" })
  ];
  assert.equal(grouping.groupPurchases(rows)[0].label, "Brighton Cafe");
  assert.equal(grouping.groupPurchases(rows.slice().reverse())[0].label, "Brighton Cafe");
});

test("a tied grouped label resolves the same way in either row order", function () {
  var rows = [
    transaction({ description: "ZEPHYR MART SINGAPORE", displayName: "Zephyr Mart" }),
    transaction({ description: "ZEPHYR MART SINGAPO", displayName: "Alpha Mart",
      date: "2026-07-02" })
  ];
  assert.equal(grouping.groupPurchases(rows)[0].label, "Alpha Mart");
  assert.equal(grouping.groupPurchases(rows.slice().reverse())[0].label, "Alpha Mart");
});

// ---------- Reversed charge/refund pairs ----------

function tripRow(id, type, amount, date, extra) {
  return Object.assign(transaction({
    id: id, type: type, amount: amount, date: date, month: date.slice(0, 7),
    description: "TRIP.COM SINGAPORE", category: "Travel"
  }), extra || {});
}

test("a charge refunded in full by the same merchant is paired and folded", function () {
  var rows = [
    tripRow("tx_c1", "debit", 412.55, "2026-08-08"),
    tripRow("tx_r1", "refund", 412.55, "2026-08-08"),
    tripRow("tx_c2", "debit", 928.65, "2026-06-28"),
    tripRow("tx_r2", "refund", 928.65, "2026-08-08"),
    tripRow("tx_keep", "debit", 76.23, "2026-06-27")
  ];
  var result = grouping.reversedPairs(rows);
  assert.equal(result.pairs.length, 2);
  assert.deepEqual(Object.keys(result.hidden).sort(), ["tx_c1", "tx_c2", "tx_r1", "tx_r2"]);
  var visible = rows.filter(function (t) { return !result.hidden[t.id]; });
  assert.deepEqual(visible.map(function (t) { return t.id; }), ["tx_keep"]);
  // Folding the pairs never moves the net.
  assert.equal(grouping.summarize(rows, {}).netCost, grouping.summarize(visible, {}).netCost);
});

test("a refund is paired only when its charge is unambiguous", function () {
  // Two identical charges before one refund: either could be the refunded one.
  var twoCharges = grouping.reversedPairs([
    tripRow("tx_a", "debit", 150.99, "2026-06-09"),
    tripRow("tx_b", "debit", 150.99, "2026-06-09"),
    tripRow("tx_r", "refund", 150.99, "2026-06-20")
  ]);
  assert.equal(twoCharges.pairs.length, 0);
  // A refund dated before its charge is not a reversal of it.
  assert.equal(grouping.reversedPairs([
    tripRow("tx_r", "refund", 50, "2026-06-01"),
    tripRow("tx_c", "debit", 50, "2026-06-02")
  ]).pairs.length, 0);
  // Beyond the window, a same-priced refund is a different story.
  assert.equal(grouping.reversedPairs([
    tripRow("tx_c", "debit", 50, "2025-06-01"),
    tripRow("tx_r", "refund", 50, "2026-06-01")
  ]).pairs.length, 0);
  // Another merchant or another amount never pairs.
  assert.equal(grouping.reversedPairs([
    tripRow("tx_c", "debit", 50, "2026-06-01", { description: "KLOOK SINGAPORE" }),
    tripRow("tx_r", "refund", 50, "2026-06-02")
  ]).pairs.length, 0);
  assert.equal(grouping.reversedPairs([
    tripRow("tx_c", "debit", 50, "2026-06-01"),
    tripRow("tx_r", "refund", 49.99, "2026-06-02")
  ]).pairs.length, 0);
  // Once a charge is claimed, a second identical refund has no candidate.
  var oneChargeTwoRefunds = grouping.reversedPairs([
    tripRow("tx_c", "debit", 50, "2026-06-01"),
    tripRow("tx_r1", "refund", 50, "2026-06-02"),
    tripRow("tx_r2", "refund", 50, "2026-06-03")
  ]);
  assert.equal(oneChargeTwoRefunds.pairs.length, 1);
  assert.deepEqual(Object.keys(oneChargeTwoRefunds.hidden).sort(), ["tx_c", "tx_r1"]);
});

test("a Trip.com booking name labels its own charge but never the merchant group", function () {
  var rows = [
    transaction({ description: "TRIP.COM SINGAPORE", category: "Travel", amount: 300,
      displayName: "Example Hotel", displayNameSource: "trip-booking",
      tripBooking: { bookingNo: "1234567890123", status: "Completed" } }),
    transaction({ description: "TRIP.COM SINGAPORE", category: "Travel", amount: 120,
      date: "2026-07-02" }),
    transaction({ description: "TRIP.COM SINGAPORE", category: "Travel", amount: 80,
      date: "2026-07-03" })
  ];
  var groups = grouping.groupPurchases(rows);
  assert.equal(groups.length, 1);
  assert.notEqual(groups[0].label, "Example Hotel");
  assert.equal(grouping.userDisplayName(rows[0]), "");
  assert.equal(groups[0].label, grouping.merchantDisplayName("TRIP.COM SINGAPORE"));
  // A name the user typed on the same merchant still wins, as before.
  rows[1].displayName = "Trip.com hotels";
  rows[1].displayNameSource = "override";
  assert.equal(grouping.groupPurchases(rows)[0].label, "Trip.com hotels");
  // Older builds carry no source marker; a bare display name is the user's.
  assert.equal(grouping.userDisplayName({ displayName: "Mine" }), "Mine");
});

test("a name you set outranks one derived from statement text", function () {
  var rows = [
    transaction({ description: "HARBOUR DELI SINGAPORE" }),
    transaction({ description: "HARBOUR DELI SINGAPO", date: "2026-07-02" }),
    transaction({ description: "HARBOUR DELI SING", displayName: "Harbour Deli",
      date: "2026-07-03" })
  ];
  assert.equal(grouping.groupPurchases(rows)[0].label, "Harbour Deli");
});

// ---------- Toggle can be driven by the page ----------

test("the page can reset the toggle without firing its callback", function () {
  var listeners = {};
  var attributes = {};
  var button = {
    addEventListener: function (name, callback) { listeners[name] = callback; },
    classList: { toggle: function () {} },
    setAttribute: function (name, value) { attributes[name] = value; }
  };
  var label = { textContent: "" };
  var states = [];
  var handle = grouping.bindToggle(button, label, function (active) {
    states.push(active);
  }, false);
  listeners.click();
  assert.equal(handle.isActive(), true);
  assert.equal(handle.set(false), true);
  assert.equal(handle.isActive(), false);
  assert.equal(label.textContent, "Group purchases");
  assert.equal(attributes["aria-pressed"], "false");
  assert.deepEqual(states, [true], "resetting must not re-enter the callback");
  assert.equal(handle.set(false), false, "resetting an off toggle is a no-op");
  listeners.click();
  assert.deepEqual(states, [true, true], "the next click turns grouping back on");
});

// ---------- Shared merchant naming ----------

test("merchantDisplayName names a description the way the ledger names a row",
  function () {
    var rows = [
      transaction({ description: "Grab* GPC-71541451439149cSINGAPORE" }),
      transaction({ description: "GRAB RIDES-EC PETALING JAYA" })
    ];
    assert.equal(grouping.merchantDisplayName(rows[0].description), "Grab");
    assert.equal(grouping.groupPurchases(rows)[0].label, "Grab");
  });

test("merchantDisplayName drops statement references but keeps the merchant",
  function () {
    var description = "HARBOUR DELI 8827361902 SINGAPORE";
    assert.equal(
      grouping.merchantDisplayName(description),
      grouping.groupPurchases([transaction({ description: description })])[0].label
    );
    assert.match(grouping.merchantDisplayName(description), /HARBOUR DELI/);
  });

// ---------- Insights, loaded the way the browser loads them ----------
//
// app/js/insights.js is a browser-global module: it assigns window.Insights and
// reads window.FinanceGrouping when a finding is built. Evaluating that exact
// file in a context holding those two globals tests what the page runs, with no
// restructuring of the module itself.

var fs = require("node:fs");
var path = require("node:path");
var vm = require("node:vm");

function loadInsights() {
  var source = fs.readFileSync(
    path.join(__dirname, "..", "app", "js", "insights.js"), "utf8");
  var context = { window: { FinanceGrouping: grouping } };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "insights.js" });
  return context.window.Insights;
}

test("names a destination from a saved override, the descriptor, or the currency", function () {
  var insights = loadInsights();
  var travel = function (overrides) {
    return transaction(Object.assign({ category: "Travel", date: "2026-03-18", month: "2026-03" }, overrides));
  };
  assert.equal(insights.travelCountry(travel({ description: "KLOOK TRAVEL SINGAPORE" })), "Unknown");
  assert.equal(insights.travelCountry(travel({ description: "KLOOK TRAVEL SINGAPORE", foreign: "CNY 120.00" })), "China");
  assert.equal(insights.travelCountry(travel({ description: "KLOOK TRAVEL SINGAPORE", foreign: "USD 12.00" })), "Unknown");
  assert.equal(insights.travelCountry(travel({ description: "KLOOK TRAVEL SINGAPORE", destination: "Japan" })), "Japan");
  // The saved destination outranks the descriptor and the currency.
  assert.equal(insights.travelCountry(travel({ description: "TOKYO DISNEY", foreign: "CNY 1.00", destination: "Taiwan" })), "Taiwan");
  assert.equal(insights.travelCountry(travel({ description: "GRAB RIDES PETALING JAYA" })), "Malaysia");
  assert.equal(insights.travelCountry(transaction({ category: "Food & dining", description: "TOKYO DISNEY" })), "");
  assert.equal(insights.travelCity(travel({ description: "Singapore (SIN) => Chengdu (TFU)" })), "Chengdu");
  assert.equal(insights.travelCity(travel({ description: "AGODA BERLIN" })), "Berlin");
  assert.equal(insights.travelCity(travel({ description: "KLOOK TRAVEL SINGAPORE" })), "");

  var rows = [
    travel({ id: "k", description: "KLOOK TRAVEL SINGAPORE", date: "2026-03-14", amount: 90 }),
    travel({ id: "h", description: "TOY STORY HOTEL SHANGHAI", date: "2026-03-17", amount: 300 }),
    travel({ id: "j", description: "TOKYO DISNEY", date: "2026-03-30", amount: 80 }),
    travel({ id: "far", description: "PARIS METRO", date: "2026-02-01", amount: 5 })
  ];
  var suggestion = insights.suggestedDestination(rows[0], rows);
  assert.equal(suggestion.country, "China");
  assert.equal(suggestion.gap, 3);
  assert.equal(insights.suggestedDestination(rows[3], rows), null);
});

test("parses Trip.com travel dates, taking a dropped year from the booking", function () {
  var insights = loadInsights();
  assert.equal(insights.parseTravelDate("18:45, October 17, 2023"), "2023-10-17");
  assert.equal(insights.parseTravelDate("October 18, 2023"), "2023-10-18");
  assert.equal(insights.parseTravelDate("May 10", "April 20, 2026"), "2026-05-10");
  // A travel day before the booking day cannot be in the booking year.
  assert.equal(insights.parseTravelDate("April 5", "April 20, 2026"), "2027-04-05");
  assert.equal(insights.parseTravelDate("09:41, May 10", "April 20, 2026"), "2026-05-10");
  assert.equal(insights.parseTravelDate("February 30, 2026"), null);
  assert.equal(insights.parseTravelDate("nonsense", "April 20, 2026"), null);
  assert.equal(insights.parseTravelDate("May 10", "nonsense"), null);
  assert.deepEqual(JSON.parse(JSON.stringify(insights.travelWindow({
    productType: "Flights", bookingDate: "January 30, 2026",
    travelTime: "22:00, March 15, 2026\n15:05, March 23, 2026"
  }))), { start: "2026-03-15", end: "2026-03-23" });
  assert.equal(insights.travelWindow({ travelTime: "" }), null);
});

test("builds trips from booking travel dates and gathers the spend around them", function () {
  var insights = loadInsights();
  function travel(overrides) {
    return transaction(Object.assign({ category: "Travel", owner: "Yx" }, overrides));
  }
  var rows = [
    // Charged in January, flown in March: the booking's travel dates anchor it.
    travel({ id: "flight", date: "2026-01-30", month: "2026-01", amount: 1000,
      description: "TRIP.COM Singapore", tripBooking: {
        productType: "Flights", bookingDate: "January 30, 2026",
        productName: "Singapore (SIN) => Shanghai (PVG)",
        travelTime: "22:00, March 15, 2026\n15:05, March 23, 2026"
      } }),
    // Hotel export drops the year; the booking date supplies it.
    travel({ id: "hotel", date: "2026-02-20", month: "2026-02", amount: 300,
      description: "TRIP.COM Singapore", tripBooking: {
        productType: "Hotels", bookingDate: "February 20, 2026",
        productName: "Toy Story Hotel", travelTime: "March 17"
      } }),
    // Bookingless charge with a known destination, six weeks ahead: joins.
    travel({ id: "visa", date: "2026-02-01", month: "2026-02", amount: 40,
      description: "CHINA VISA CENTRE SHANGHAI" }),
    // Foreign-currency spend inside the window counts even outside Travel.
    transaction({ id: "dinner", date: "2026-03-18", month: "2026-03", amount: 15,
      category: "Food & dining", description: "SHANGHAI NOODLES", foreign: "CNY 69.00" }),
    travel({ id: "metro", date: "2026-03-19", month: "2026-03", amount: 5,
      description: "SHANGHAI METRO", foreign: "CNY 25.00" }),
    // Local groceries the same week are not part of the trip.
    transaction({ id: "groceries", date: "2026-03-18", month: "2026-03", amount: 40,
      category: "Groceries", description: "CORNER STORE" }),
    // A hotel booked, cancelled and refunded in full: both rows sit inside
    // the trip so its total is unchanged, but neither is a confirmed charge
    // and the cancelled booking is not a booking.
    travel({ id: "cancelled", date: "2026-02-25", month: "2026-02", amount: 200,
      description: "TRIP.COM Singapore", tripBooking: {
        productType: "Hotels", status: "Cancelled", bookingDate: "February 25, 2026",
        productName: "Some Hotel Shanghai", travelTime: "March 18"
      } }),
    travel({ id: "refund", type: "refund", date: "2026-02-28", month: "2026-02", amount: 200,
      description: "TRIP.COM Singapore", tripBooking: {
        productType: "Hotels", status: "Cancelled", bookingDate: "February 25, 2026",
        productName: "Some Hotel Shanghai", travelTime: "March 18"
      } }),
    // Charges the other person paid: a flight for the same trip, and a
    // dinner in CNY inside the window. They join the trip and name it, but
    // their money is reported apart from this tracker's own.
    travel({ id: "nic_flight", paidBy: "Nic", date: "2026-03-16", month: "2026-03", amount: 500,
      description: "SINGAPOREAIR 1234567890" }),
    transaction({ id: "nic_dinner", paidBy: "Nic", date: "2026-03-19", month: "2026-03", amount: 15,
      category: "Food & dining", description: "SHANGHAI DUMPLINGS", foreign: "CNY 70.00" }),
    // A separate journey later in the year.
    travel({ id: "tokyo", date: "2026-06-01", month: "2026-06", amount: 80,
      description: "TOKYO DISNEY RESORT" }),
    travel({ id: "narita", date: "2026-06-03", month: "2026-06", amount: 30,
      description: "NARITA EXPRESS" })
  ];
  var trips = insights.buildTrips(rows);
  assert.equal(trips.length, 2);
  assert.equal(trips[0].primary, "Japan");
  assert.equal(trips[0].start, "2026-06-01");
  assert.equal(trips[0].end, "2026-06-03");
  assert.equal(trips[0].days, 3);
  assert.equal(trips[0].count, 2);
  assert.equal(trips[0].anchored, false);
  var china = trips[1];
  assert.equal(china.primary, "China");
  assert.equal(china.city, "Shanghai");
  assert.equal(china.start, "2026-03-15");
  assert.equal(china.end, "2026-03-23");
  assert.equal(china.days, 9);
  assert.deepEqual(Array.from(china.ids).sort(),
    ["cancelled", "dinner", "flight", "hotel", "metro", "nic_dinner", "nic_flight", "refund", "visa"]);
  assert.equal(china.rowCount, 7);
  assert.equal(china.count, 5);
  assert.equal(china.bookings, 2);
  assert.equal(china.partnerTotal, 515);
  assert.equal(china.partnerCount, 2);
  assert.equal(china.paidBy, "Nic");
  assert.equal(insights.confirmedTravelCharges(rows).map(function (t) { return t.id; }).sort().join(","),
    "flight,hotel,metro,narita,tokyo,visa");
  assert.equal(china.anchored, true);
  assert.equal(china.total, 1360);
  assert.equal(china.perDay, Math.round(1360 / 9 * 100) / 100);
  assert.deepEqual(JSON.parse(JSON.stringify(china.split)), {
    "Flights": 1000, "Hotels": 300, "Tickets & transfers": 0, "On the ground": 60
  });
});

function accountRow(month, direction, flow, amount) {
  return {
    id: [month, direction, flow, amount].join("-"),
    month: month,
    date: month + "-15",
    direction: direction,
    flow: flow,
    amount: amount
  };
}

// Six statement months of card rows, so the account-level section clears its
// own six-month threshold and the month-vs-baseline section has a history.
function cardDataset(extraRows) {
  var months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"];
  var rows = months.map(function (month, index) {
    return transaction({
      id: "card-" + month,
      description: "CORNER STORE " + index,
      category: "Groceries",
      month: month,
      date: month + "-08",
      amount: 40
    });
  });
  return {
    months: months,
    transactions: rows.concat(extraRows || []),
    salarySteps: [],
    settlements: {},
    // A stale rollup that build_data.py never emits: the findings below must
    // come from the account rows, so these must make no difference at all.
    accountMonths: [],
    accounts: {}
  };
}

function accountDataset(months) {
  var rows = [];
  months.forEach(function (month, index) {
    // Interest halves across the span; salary and card bills stay flat.
    rows.push(accountRow(month, "deposit", "Interest",
      index === 0 ? 100 : (index === months.length - 1 ? 50 : 70)));
    rows.push(accountRow(month, "deposit", "Salary", 1000));
    rows.push(accountRow(month, "withdrawal", "Credit card bill", 300));
    // Noise the totals must ignore.
    rows.push(accountRow(month, "withdrawal", "Transfer", 900));
    rows.push(accountRow(month, "deposit", "Transfer", 25));
  });
  return { months: months.slice(), transactions: rows };
}

function findInsight(items, pattern) {
  return items.filter(function (item) { return pattern.test(item.title); })[0];
}

test("interest and card-spending findings derive from the account rows",
  function () {
    var months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"];
    var items = loadInsights().build(
      cardDataset(), "2026-06", accountDataset(months));
    var interest = findInsight(items, /^Interest earned is/);
    assert.ok(interest, "the interest trend must render from account deposits");
    assert.equal(interest.title, "Interest earned is down 50%");
    var cards = findInsight(items, /^Card spending is/);
    assert.ok(cards, "the card-spending share must render from account rows");
    // 6 x S$300 of card bills against 6 x S$1,000 of salary credited.
    assert.equal(cards.title, "Card spending is 30% of income");
  });

test("account-level findings keep their six-month threshold", function () {
  var months = ["2026-03", "2026-04", "2026-05", "2026-06"];
  var items = loadInsights().build(
    cardDataset(), "2026-06", accountDataset(months));
  assert.equal(findInsight(items, /^Interest earned is/), undefined);
  assert.equal(findInsight(items, /^Card spending is/), undefined);
});

test("account months after the selected month never enter a finding", function () {
  var months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"];
  var account = accountDataset(months.concat(["2026-07", "2026-08"]));
  var items = loadInsights().build(cardDataset(), "2026-06", account);
  var cards = findInsight(items, /^Card spending is/);
  assert.ok(cards);
  assert.match(cards.detail, /over the last 6 months/);
});

test("a habit is keyed and named exactly as the ledger groups it", function () {
  var months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"];
  var grabRows = [];
  months.forEach(function (month, index) {
    ["Grab* GPC-71541451439149cSINGAPORE", "GRAB RIDES-EC PETALING JAYA"]
      .forEach(function (description, slot) {
        grabRows.push(transaction({
          id: "grab-" + month + "-" + slot,
          description: description,
          category: "Transport",
          month: month,
          date: month + "-1" + (slot + index % 2),
          amount: 12
        }));
      });
  });
  var items = loadInsights().build(
    cardDataset(grabRows), "2026-06", { months: [], transactions: [] });
  var habit = findInsight(items, /^Grab:/);
  assert.ok(habit, "12 charges over 6 months is a habit");
  assert.equal(habit.filterLabel, "Grab");
  assert.equal(habit.ids.length, 12, "the drill-down carries every counted row");
  // The ledger's own grouping is the reference: one merchant, same label.
  var groups = grouping.groupPurchases(grabRows);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].label, habit.filterLabel);
  assert.equal(groups[0].ids.length, habit.ids.length);
});

test("a name you set outranks statement text in a habit and a largest charge",
  function () {
    var months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"];
    var rows = [];
    months.forEach(function (month, index) {
      [0, 1].forEach(function (slot) {
        rows.push(transaction({
          id: "ride-" + month + "-" + slot,
          description: "Grab* GPC-" + index + slot + "SINGAPORE",
          displayName: "Rides",
          category: "Transport",
          month: month,
          date: month + "-1" + slot,
          amount: 12
        }));
      });
    });
    rows.push(transaction({
      id: "big-2026-06",
      description: "SOME AIRLINE 998877 SINGAPORE",
      displayName: "Flight home",
      category: "Travel",
      month: "2026-06",
      date: "2026-06-20",
      amount: 400
    }));
    var items = loadInsights().build(
      cardDataset(rows), "2026-06", { months: [], transactions: [] });
    var habit = findInsight(items, /^Rides:/);
    assert.ok(habit, "the habit is titled with the name you set");
    var largest = findInsight(items, /^Largest charge:/);
    assert.ok(largest);
    assert.equal(largest.filterLabel, "Flight home");
    assert.match(largest.title, /at Flight home$/);
    assert.equal(largest.ids.length, 1);
    assert.equal(largest.ids[0], "big-2026-06");
  });

test("spending summary states the change, driver and largest purchase", function () {
  var data = cardDataset([
    transaction({
      id: "shopping-2026-06",
      description: "SHOPEE SINGAPORE",
      category: "Shopping",
      month: "2026-06",
      date: "2026-06-20",
      amount: 180,
      shopee: { items: ["Standing desk"], merchant: "Office Shop" }
    })
  ]);
  var summary = loadInsights().summarize(data, "2026-06");
  assert.equal(summary.kind, "warn");
  assert.match(summary.text, /higher than your recent typical month/);
  assert.match(summary.text, /Shopping was the main driver/);
  assert.match(summary.text, /largest charge was S\$180\.00 for Standing desk/);
});

test("travel countries use booking destinations instead of Singapore billing text", function () {
  var insights = loadInsights();
  assert.equal(insights.travelCountry({
    category: "Travel",
    description: "Trip.com Singapore",
    displayName: "Singapore (SIN) => Shanghai (PVG)"
  }), "China");
  assert.equal(insights.travelCountry({
    category: "Travel",
    description: "Trip.com Singapore",
    tripBooking: { productName: "Comfort Inn Yeouido", productType: "Hotels" }
  }), "South Korea");
});

test("generic travel platforms stay unknown until destination evidence exists", function () {
  var insights = loadInsights();
  assert.equal(insights.travelCountry({
    category: "Travel",
    description: "Klook Travel Singapore"
  }), "Unknown");
  assert.equal(insights.travelCountry({
    category: "Shopping",
    description: "agoda.com Berlin"
  }), "");
});

test("income forecast separates recurring pay from repeated bonus months", function () {
  var insights = loadInsights();
  var months = [];
  var rows = [];
  [2024, 2025, 2026].forEach(function (year) {
    var lastMonth = year === 2026 ? 8 : 12;
    for (var month = 1; month <= lastMonth; month += 1) {
      var key = year + "-" + String(month).padStart(2, "0");
      var base = year === 2024 ? 5000 : year === 2025 ? 5500 : 6000;
      months.push(key);
      rows.push({
        month: key, direction: "deposit", flow: "Salary", amount: base,
        description: "Agency payroll 123456789"
      });
      rows.push({
        month: key, direction: "deposit", flow: "Salary", amount: 50,
        description: "Recurring payroll allowance 987654321"
      });
      if (month === 5) rows.push({
        month: key, direction: "deposit", flow: "Salary", amount: base * 2,
        description: "Agency payroll 123456789"
      });
      if (month === 12) rows.push({
        month: key, direction: "deposit", flow: "Salary", amount: base,
        description: "Agency payroll 123456789"
      });
    }
  });
  var forecast = insights.incomeForecast(rows, months);
  assert.equal(forecast.baseMonthly, 6050);
  assert.deepEqual(Array.from(forecast.completeYears), ["2024", "2025"]);
  assert.deepEqual(Array.from(forecast.bonusPatterns, function (item) { return item.month; }), [5, 12]);
  assert.ok(forecast.forecastCentral > forecast.forecastFloor);
  assert.equal(forecast.futurePatterns.length, 1);
  assert.equal(forecast.futurePatterns[0].month, 12);
});
