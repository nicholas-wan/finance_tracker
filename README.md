# UOB finance tracker

A private local dashboard for UOB credit-card and ONE account statements:
spending, income, investments, insurance, the household register, net worth,
shared expenses, game sales, balances, data quality and suspicious-transaction
reviews. Plain HTML, CSS and JavaScript; no build step, no CDN.

## Run

```powershell
./scripts/launch_dashboard.ps1 -Port 3403
```

Open http://localhost:3402. The editable server binds to `127.0.0.1` only and
is required for saving anything. The desktop **Finances** shortcut runs
`scripts/launch_dashboard.vbs`, which starts one server without a console
window, reuses a healthy hand-started server, and stops its own server about
fifteen seconds after the last dashboard tab closes (two minutes after a
browser crash). `python -m http.server 3402 --directory app` gives a view-only
copy.

**Share on Wi-Fi** (header button, editable server only) serves a read-only
copy on port 4402 (`--share-port`) behind a random code copied to the
clipboard as `http://<this PC>:4402/?k=<code>`. It refuses every write, never
lists folders, and stops after two hours, on **Stop sharing**, or when the
server exits. Editing never leaves this computer.

### A second household copy

Clone the repository again rather than mixing two people's data; `statements/`,
`manual/` and `app/data/` are Git-ignored, so a clone carries only the app.

```powershell
git clone <repository-url> UOB_finance_yx
Set-Location UOB_finance_yx
New-Item -ItemType Directory -Force manual | Out-Null
Copy-Item examples/identity.example.json manual/identity.json
```

Edit `manual/identity.json`, add PDFs under `statements/<year>/`, run
`python scripts/import_all.py --strict`, and launch with
`./scripts/launch_dashboard.ps1 -Port 3403` if both must run together. Give
the clone its own identity without touching tracked files: copy
`examples/branding.example.json` to `manual/branding.json` (port, monogram,
title, light and dark brand colours, and `tabs`: which tabs this clone shows
and in what order, so each person keeps the features they use and the
header stays readable) and drop a `favicon.svg` or
`favicon.ico` into `manual/branding/`; the server and the launcher take
their default port from it, the colours are published as
`data/branding.json`, and the private favicon is served in place of the
tracked one. Both clones run
the same code: a tab shows whatever its data holds, so a clone without
`home.json` or trip bookings simply shows those tabs empty.

## Import statements

```powershell
python -m pip install -r requirements.txt
python scripts/import_all.py
```

Parsing fails closed: an unreadable row, unreconciled card section, contradictory
amount or broken balance chain aborts the run without touching published data.
The importer builds and validates all generated files in a staging directory
under one generation ID, then publishes last, so a failed or overlapping run
never exposes a half-new dataset. `--strict` publishes only when categories,
ownership, provenance and suspicious checks are also clear;
`python scripts/validate_data.py --strict` applies the same gate to the
published files. Statement months come from PDF contents; a second source for
an existing month is rejected.

## Dashboard

The header is two rows: the brand row (logo, wordmark, share and theme)
scrolls away, and the tab row beneath it stays pinned to the top of the
window. The logo is the way to the Overview; there is no Overview tab.
Income and Net worth are sub-tabs of **Wealth**, and Games is a sub-tab of
**Transactions**, so the tab row holds at most six tabs; each clone lists
the ones it shows in `manual/branding.json`.

- **Overview** — statement freshness, card-fee waiver prompts, KPIs, spending
  summary, categories, outflows and the month's ledger. 6M/12M figures on the
  Transactions page are means; overview baselines are medians. **Year so
  far** sums the selected month's year to date (income, card spending,
  invested, kept) and compares with the same months a year earlier when all
  of them have statements. **Recurring charges** lists every merchant or
  payee that charges at a steady interval (weekly to yearly) and a steady
  amount, detected from the rows alone after the third charge: latest
  amount against the usual one, cadence, last and next date, and a status of
  due, not seen since, or stopped (no charge for over two intervals); the
  summary states the monthly equivalent. Insurance premiums are left to the
  Insurance tab. Each row carries the bundled brand logo where one matches,
  otherwise an initials badge; selecting a row opens it in Transactions.
- **Income** — the hand-entered salary sheet (`manual/salary.json`) and an
  outlook derived from salary-labelled bank credits: recurring payroll sets
  the base, a bonus month is forecast only when it beat ordinary pay by 25% in
  both of the latest complete years, and 3/5/10-year cards use 3%, 5% and 7%
  growth. **How this forecast is calculated** shows the live formula.
- **Insurance** — premiums, coverage and policies per person from
  `manual/insurance.json`. Scheduled premiums and posted payments are kept
  separate; a policy paid by GIRO reconciles against bank statements,
  everything else against card statements. Matured and lapsed policies sit in
  an archive. Portal-checked policies carry a static **Verified** badge.
- **Home** — the household register in Git-ignored `manual/home.json`:
  appliances, fixtures, furniture and pet items with costs, key dates,
  warranty cover and documents; home and fire policies; the mortgage; and
  maintenance, including prepaid multi-visit plans as one dated schedule.
  Items have List, Grid and Coverage (warranty timeline) views, a category and
  zone rail, and warranty and information filters. Items are read-only in the
  browser and maintained by the assistant from documents in the Drive
  **Warranty** folder (`[Category] Brand Model - Document - YYYY-MM-DD.pdf`);
  insurance, mortgage and maintenance records have forms. **Needs attention**
  lists policy, mortgage-review, service and warranty-expiry dates within 90
  days plus unverified records. Saves use origin checks, a write lock, atomic
  replacement, rotating backups and stale-edit rejection;
  `app/data/home.json` is the read-only snapshot. The owner's standing
  decisions on what is and is not verified are in `CLAUDE.md`; as of
  5 September 2026 every item is Verified with a document and a settled
  warranty state.
- **Net worth** — assets minus liabilities. Dated balance snapshots recorded
  by hand live in Git-ignored `manual/net_worth.json` (accounts grouped as
  cash, CPF, investments, insurance, property, liabilities). Derived series
  come from data already present: the UOB ONE closing balance on every
  statement and the mortgage balance on the Home register. A value carries
  forward until the next snapshot, so each account shows its as-at date and
  anything over three months old is marked. The history chart stacks groups
  by month with liabilities below zero; the 12-month change counts only
  accounts recorded at both dates, and a second figure excludes property and
  the home loan. `/api/net-worth` saves one account, snapshot or deletion per
  request with the same guarantees as Home; `app/data/net_worth.json` is the
  read-only snapshot. A final panel totals money moved from the bank account
  to brokers, SRS and fixed deposits as a reference, never a valuation.
  Insurance counts at net surrender value, not premiums paid.
- **Travel** — the whole travel history: this year against earlier years,
  spending by country and by year, and the trips. A trip is the cluster of
  Travel-category charges belonging to one journey; a charge matched to a
  Trip.com booking (`manual/trip_bookings.json`, from Trip.com's **All
  Bookings → Export**) is anchored at the booking's travel dates, a Klook
  order (`manual/klook_orders.json`, captured by hand from klook.com/bookings)
  or a WeChat Pay payment on YouTrip (`manual/wechat_payments.json`, via
  `scripts/import_wechat_statement.py`) joins by date, and a charge with no
  evidence lands on the nearest trip within a week. Destinations come from
  bookings, then currency, then a per-charge override. What the other
  household member paid for shared travel appears too:
  `scripts/import_partner_travel.py` copies their Travel rows from their
  clone's published data into `manual/partner_travel.json`, shown beside the
  statements and never inside them, with a **Paid by** filter. Every panel
  opens the Transactions tab already filtered to that trip, country or year.
- **Games** — game-account sales from `manual/game_sales.json`, kept off the
  statements.
- **Split** — the Yx settlement shared with the Overview: opening balances
  carry forward, `Yx share` is half of `Shared` plus rows assigned to `Yx`.
- **Transactions** — period, search, category/flow, owner, direction and
  review filters. Owner chips tag a row (or a merchant group, or the filtered
  list up to 100 rows) in one click; clicking the active chip clears the tag
  and hands the row back to `manual/owner_rules.json`. A charge refunded in
  full by the same merchant within 180 days folds away with its refund.
  Recognised brands show a locally bundled icon; no third-party logo service
  is contacted. **Foodpanda**, **Shopee** and **Grab** views match imported
  order history to card charges only where the match is safe, and show order
  details in the drawer. **Review transaction** opens the bank row needing
  attention; decisions are saved and audited per signal. **History** lists
  manual changes.

Manual edits use stable content-based transaction IDs, so PDF renames or
extraction shifts do not detach decisions. Saves and rebuilds are atomic and
roll back on validation failure.

Across the page: data files are fetched once and shared by every tab
(`app/js/data-cache.js`), and the server gzips JSON, script and stylesheet
responses, so the multi-megabyte ledgers travel as a few hundred kilobytes.
Press `/` anywhere to jump to the transaction search. On a phone the tab bar
and the merchant strip scroll sideways with a faded edge. Printing shows the
active tab as a plain document. If the code on disk changes after the server
started, a banner asks for a restart instead of letting saves fail on a route
the running process does not have.

## Accounting rules

- Interactive Brokers, SRS, fixed deposits, transfers and card-bill payments
  are movements of money, not spending.
- Card `Payment` and `Rebates` are excluded from spending; refunds reduce totals.
- Salary figures are gross manual values, never inferred from deposits.

## Common changes

| What | Where |
|---|---|
| Merchant category and game seller rules | `CATEGORY_RULES`, `GAME_RULES` in `scripts/build_data.py` |
| Salary history, game sales, settlements | `manual/salary.json`, `manual/game_sales.json`, `manual/settlements.json` |
| Insurance policies | `manual/insurance.json` (see the file's `summary`, `verification`, `components`, `coverageOnly`, `premiumPaidBy`, `hiddenInRegister`, `reconcileWithImportedStatements` fields) |
| Foodpanda, Shopee, Grab history | `manual/foodpanda_orders.json`, `manual/shopee_orders.json` (`statementOrderMaxHistoryIndex`, `statementAggregates`), `manual/grab_receipts.json` via `scripts/import_grab_receipts.py`, `manual/grab_web_history.json` |
| Trip.com bookings, Klook orders, WeChat Pay on YouTrip, the other member's travel | `manual/trip_bookings.json` (`scripts/import_trip_bookings.py`), `manual/klook_orders.json`, `manual/wechat_payments.json` (`scripts/import_wechat_statement.py`), `manual/partner_travel.json` (`scripts/import_partner_travel.py`) |
| Clone identity | `manual/branding.json` and `manual/branding/favicon.*` (see `examples/branding.example.json`) |
| Statement-holder name, own accounts, trusted counterparties, Grab location aliases | `manual/identity.json` (required; the parser refuses to run without it) |
| Owner-policy preview | `python scripts/assign_unassigned.py` (`--apply` to save; refuses to run beside a live server) |
| Suspicious-check thresholds | top of `scripts/risk_checks.py` and `analyzeAccountTransactions` in `app/js/transaction-grouping.js` |

All `manual/` files are Git-ignored. Rebuild after changing them.

## Layout, backup, restore

| Path | Purpose |
|---|---|
| `statements/<year>/` | Source PDFs and older CSV exports |
| `manual/` | Owners, overrides, remarks, reviews, settlements, salary, game sales, identity, home, net worth |
| `scripts/` | Parsers, data builder, validation, local server |
| `app/` | Dashboard source and generated `app/data/` JSON |
| `tests/` | Parser, classification, API, risk and grouping regression tests |

Back up `statements/` and `manual/`; `app/data/` is regenerated:

```powershell
Compress-Archive -Path statements, manual -DestinationPath "$env:USERPROFILE\Documents\UOB_backups\UOB_data_$(Get-Date -Format yyyy-MM-dd).zip"
```

Every save also keeps the newest 30 timestamped copies of each changed file in
`manual/backups/`; copy a timestamp's files back and rerun
`python scripts/build_data.py` to undo. The server seeds its own empty manual
files on a fresh clone.

## Safeguards

Private identity lives only in Git-ignored `manual/identity.json`; the write
API accepts the dashboard's own origin only; every save keeps rotating
backups. Classifier tokens match at word edges and a rule can veto phrases
that contain its token (GIANT LEAP is not a supermarket, COFFEE TABLE is not
a cafe). Suspicious checks include same-day duplicates, first-seen
high-value merchants, spikes and outliers, bursts, subscription jumps,
first foreign-currency use, unmatched large credits, **card testing**
(three or more charges of S$20 or less on one day from merchants never seen
before) and **new-merchant velocity** (a merchant first seen within three
days already charging on its third day for S$100 or more). Identical
same-day charges are numbered by their printed page and line, so a parser
change cannot swap their IDs. `./scripts/install_hooks.ps1` installs a
pre-commit hook that refuses a commit whose staged diff adds the
statement-holder name, an own-account number, a full trusted-counterparty
name, an NRIC/FIN or an account-number shape (`--no-verify` bypasses a
deliberate exception).

## Limitations

Categories and suspicious checks are rule-based; statements carry no
receipts, device data, precise time, merchant category codes or order
details, so vague descriptors may still need manual review.
