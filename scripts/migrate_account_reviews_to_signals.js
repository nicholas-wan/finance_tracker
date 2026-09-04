"use strict";

// One-shot migration from row-only bank acknowledgements to the exact checks
// that were visible when each row was acknowledged.
const fs = require("fs");
const path = require("path");
const grouping = require("../app/js/transaction-grouping.js");

const root = path.resolve(__dirname, "..");
const reviewPath = path.join(root, "manual", "account_reviews.json");
const accountPath = path.join(root, "app", "data", "account_transactions.json");
const dashboardPath = path.join(root, "app", "data", "transactions.json");
const write = process.argv.includes("--write");

const reviews = JSON.parse(fs.readFileSync(reviewPath, "utf8"));
const legacy = Array.isArray(reviews.reviewedIds) ? reviews.reviewedIds : [];
if (!legacy.length) {
  console.log("No row-only bank reviews need migration.");
  process.exit(0);
}
const account = JSON.parse(fs.readFileSync(accountPath, "utf8"));
const dashboard = JSON.parse(fs.readFileSync(dashboardPath, "utf8"));
grouping.configure(dashboard.identity || {});
const analysis = grouping.analyzeAccountTransactions(account.transactions || [], {});
const byId = new Set((account.transactions || []).map(function (row) { return row.id; }));
const missing = legacy.filter(function (id) { return !byId.has(id); });
if (missing.length) throw new Error(missing.length + " reviewed IDs no longer exist; refusing migration");

const now = new Date().toISOString();
const signals = legacy.map(function (id) {
  return { id: id, checks: (analysis[id] && analysis[id].checks || []).slice().sort(), recognizedAt: now };
}).filter(function (entry) { return entry.checks.length > 0; });
console.log("Mapped " + signals.length + " of " + legacy.length + " bank reviews to current signals.");
if (!write) {
  console.log("Dry run. Re-run with --write to save.");
  process.exit(0);
}
const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 20);
fs.copyFileSync(reviewPath, reviewPath + ".pre_signals_" + stamp + ".bak");
const payload = Object.assign({}, reviews, { recognizedSignals: signals });
delete payload.reviewedIds;
const temporary = reviewPath + ".tmp";
fs.writeFileSync(temporary, JSON.stringify(payload, null, 1) + "\n", "utf8");
fs.renameSync(temporary, reviewPath);
console.log("Migrated bank reviews to per-signal recognition.");
