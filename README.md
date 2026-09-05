# UOB finance tracker

A private local dashboard for Yx's UOB credit-card and ONE account statements. It
tracks spending, income, investments, insurance, balances, imported merchant
history, data quality, and suspicious-transaction reviews. This clone is a
single-owner ledger: every imported charge belongs to Yx. The app is plain HTML,
CSS, and JavaScript with no build step or external CDN.

## Run locally

```powershell
./scripts/launch_dashboard.ps1 -Port 3403
```

Open http://localhost:3403. Port 3402 is reserved for the separate Nic tracker.
The editable server binds only to `127.0.0.1` and is required for saving edits
and review decisions.

### Desktop or taskbar launcher

The repository includes `scripts/launch_dashboard.vbs` and
`scripts/launch_dashboard.ps1`. The desktop **Yx Finances** shortcut runs them
without opening a PowerShell window, starts one editable server on port 3403,
and opens the dashboard. It uses `app/favicon.ico`, the purple Yx mark, so it
sits next to the green Nic tracker icon without confusion. Right-click the
shortcut and choose **Show more options → Pin to taskbar**. To recreate the
shortcut, run this in PowerShell:

```powershell
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\Yx Finances.lnk")
$s.TargetPath = "C:\Windows\System32\wscript.exe"
$s.Arguments = '//nologo "' + (Resolve-Path .\scripts\launch_dashboard.vbs) + '"'
$s.WorkingDirectory = (Resolve-Path .)
$s.IconLocation = (Resolve-Path .\app\favicon.ico).Path + ",0"
$s.Save()
```

Launcher-started servers track open dashboard tabs. Closing the last tab normally
stops the server within about fifteen seconds; the grace period is long enough that
reloading the page on a busy machine does not stop the server behind it. After a
browser crash, the heartbeat timeout stops it within about two minutes. Multiple
dashboard tabs are supported. Running `python scripts/serve.py` manually remains
persistent until `Ctrl+C`; the launcher reuses a healthy manually started server and
only opens the dashboard against it, rather than replacing it.

For a view-only dashboard on this computer without the editable server (use
another port if the editable server is already on 3403):

```powershell
python -m http.server 3404 --directory app
```

### Share on Wi-Fi

The **Share on Wi-Fi** button in the header (editable server only) opens a
read-only copy of the dashboard on every network interface on port 4403
(`--share-port` overrides it), copies a link of the form
`http://<this PC's address>:4403/?k=<code>` to the clipboard, and shows it with
**Copy** and **Stop sharing** buttons. The copy runs inside the dashboard
process, so it cannot outlive it; it answers only to requests carrying the
random code (the first visit stores it as a cookie), never lists folders, and
refuses every write. Whoever has the link can read every statement row,
including the booking number and traveller on matched Trip.com charges, so the
share stops by itself after two hours, when you press Stop, or when the
dashboard server exits. While a share is live the server ignores auto-stop, so
closing your own tab does not cut the guest off. Windows may ask once to allow
Python through the firewall on private networks. Editing never leaves this
computer.

## Create a separate household copy

Use a separate clone rather than putting two people's source data in one working
directory. Private inputs and generated output are Git-ignored, so an ordinary
clone copies the application but not `statements/`, `manual/`, `app/data/`, or
backups from this tracker.

```powershell
git clone <repository-url> UOB_finance_yx
Set-Location UOB_finance_yx
New-Item -ItemType Directory -Force manual | Out-Null
Copy-Item examples/identity.example.json manual/identity.json
```

Edit `manual/identity.json`, place the other person's PDFs under
`statements/<year>/`, then run `python scripts/import_all.py --strict`. If both
trackers need to be open together, give the second one a different local port:

```powershell
./scripts/launch_dashboard.ps1 -Port 3403
```

Do not copy this repository's `manual/`, `statements/`, or `app/data/` folders
into the new clone. Back up each clone's private folders separately.

Give the clone its own header identity: change the monogram text in the
`.brand-mark` SVG in `app/index.html`, the `--brand` and `--brand-ink` colours
(light and dark) at the top of `app/css/styles.css`, and `app/favicon.svg`.

## Import statements

Place PDFs in `statements/<year>/`, then run:

```powershell
python -m pip install -r requirements.txt
python scripts/import_all.py
```

Parsing fails closed: an unreadable row, unreconciled card section, contradictory
amount, or broken balance chain aborts the run without replacing generated data.
The importer parses and validates all three generated files in a private staging
directory, stamps them with one generation ID, then publishes the dashboard last.
This prevents a failed or overlapping import from exposing a half-new dataset.
Validation checks stable IDs, dates, provenance, account continuity, manual
references, and source-to-dashboard totals. Audit history is exempt from reference
checks because history may outlive the rows it describes. Use `python
scripts/import_all.py --strict` to publish only when unresolved categories,
ownership, provenance, and suspicious checks are also clear. Run
`python scripts/validate_data.py --strict` to apply the same strict gate to the
currently published files without importing anything.

Statement months come from PDF contents. Importing a second source for an existing
month is rejected, while legitimate identical transactions within a statement are
preserved.

## Dashboard

The Transactions page supports period, search, category/flow, owner, direction, and
review-status filters.

- The header identifies the tracker at a glance: a **Yx** monogram tile beside the
  wordmark, with the purple brand colour on the top bar, a faint header tint, and the
  wordmark, in both themes.
- The **Income** tab derives its history from salary-labelled bank credits; empty
  hand-entered salary-sheet panels disappear automatically. The outlook separates
  recurring payroll streams from variable pay, detects a bonus month only when it
  exceeds the ordinary-pay baseline by at least 25% in both latest complete years,
  and forecasts any remaining repeated bonus months separately. The current-year
  estimate therefore includes actual bonuses already received without spreading an
  early lump sum over every remaining month. Three-, five-, and ten-year scenarios
  show 3%–7% annual growth around a 5% midpoint. **How this forecast is calculated**
  expands to the live formula, assumptions, detected months, and the Public Service
  Division references used to distinguish mid-year/year-end AVC, NPAA/13th-month,
  and performance-linked components. Published civil-service bonus multiples are
  context only; the amounts come from this account's own history because agency and
  individual awards may differ.
- Card summaries show net cost after refunds, category and owner breakdowns, and
  6M/12M comparisons. **Group purchases** combines normalized merchants without
  merging the source rows.
- A charge refunded in full by the same merchant (same amount, refund on or
  after the charge, within 180 days, and no other candidate charge) is folded
  away with its refund by default; the footer's **Show N refunded charges**
  button brings both rows back, badged. Folding never changes the net cost.
- Recognized recurring brands show a compact locally bundled icon beside the
  transaction. Unmatched merchants stay text-only, and the browser never contacts
  a merchant or third-party logo service to render the ledger.
- Bank summaries show opening and closing balances, reconciliation, money movement,
  non-transfer spending, and 6M/12M spending averages. Bank rows can be grouped by
  counterparty; transfers and internal movements can be hidden.
- **Review transaction** opens the exact bank row needing attention. Review prompts
  explain their reason and are not fraud verdicts. Decisions are saved and audited.
  Recognition is per signal, not per transaction: a new kind of alert on an already
  recognized transaction re-surfaces. Card checks net refunds against charges from
  the preceding week per merchant before flagging; a credit never nets a charge
  that came after it.
- Owner chips in the Owner column tag a row in one click. Clicking the chip that
  is already active clears the tag, handing the row back to the merchant
  fallbacks in `manual/owner_rules.json` rather than pinning it as unassigned.
  With **Group purchases** on, one chip retags every row in the merchant group,
  and the table footer can tag the whole filtered list (up to 100 rows) at once.
  Batches save under a single rebuild and a single audit entry, and roll back
  whole if any row fails to apply.
- Opening any transaction shows its original statement description and provenance.
  Card transactions also support display-name, category, owner, and remark edits.
  A drawer save only writes an owner tag when the owner itself changed, so
  editing a category no longer silently confirms the owner it was showing, and
  choosing **Unassigned** there clears the tag exactly like the chip does,
  handing the row back to the `manual/owner_rules.json` fallbacks.
- **History** shows recorded manual changes.
- **Foodpanda only** filters Transactions to exact order/statement matches.
  Those rows show the actual merchant with a Foodpanda logo; opening one shows
  its order number, fulfilment type, timestamp, and total. Mismatches stay in
  the imported data instead of being forced onto an unrelated card charge.
- **Shopee only** uses the same Transactions-page pattern: matched charges show
  the item and seller with a Shopee logo, and the drawer shows the order number,
  status, total, and every item name captured from the purchase card. Since
  Shopee hid order dates behind a slider check, only amount groups with equal
  order/statement cardinality inside the statement-era history are linked.
  Reviewed bundled charges can be recorded in `statementAggregates`; each entry
  names one stable transaction ID, two or more order IDs, and an evidence note,
  and the order totals must add to the statement charge exactly. The ledger
  displays those bundles as one row per order, all on the statement date, while
  preserving the combined charge as the source evidence.
- **Trip.com only** shows every Trip.com statement row: named bookings,
  charges that could not be matched safely, and refunds, so the month's Trip.com
  net cost is complete. Booking history comes from the site's **My Bookings**
  Excel exports, merged by `scripts/import_trip_bookings.py` (later exports win
  on a repeated booking number). A card charge shows the actual hotel, flight,
  attraction, or transfer name automatically when it and one booking are the
  sole pair within seven days with exactly the same SGD amount. Reviewed links
  in `manual/trip_booking_reconciliation.json` cover discounts, split payments,
  aggregate charges, refunds, and replacement-booking price adjustments by
  naming stable transaction IDs and booking numbers explicitly. The drawer
  shows the reconciliation evidence and every booking when one statement row
  covers several. Unreviewed, foreign-currency, and undated rows keep the
  original statement description rather than being assigned by a loose amount
  tolerance. A booking that was later cancelled remains marked **Cancelled**.
  Opening a matched transaction shows the original statement description and
  a **Trip.com booking** section with status, type, booking number, dates,
  traveller, and total. The booking name is a derived label: the drawer's
  display-name field shows it as a placeholder, so saving a remark does not
  freeze it as an override, and it never becomes the label of a grouped
  Trip.com row. The quality summary counts matched bookings, charges, refunds,
  cancellations, ambiguous charges, and unmatched rows; the validator
  re-derives every published link.
- The **Travel** tab shows the whole travel history: this year so far against
  the same months of last year, the all-time total, the trip count with the
  median cost per day, the latest trip taken and the next one booked, every
  trip as a card named by its main city and country ("Chengdu, China") under
  a divider per year (trips that have not started yet sit
  under **Upcoming**) with year pills to narrow the list (the latest year is
  selected by default), and bars by country/region with charge counts and
  shares and by statement year. Amounts everywhere are net of refunds, and
  every count is of confirmed charges only: a debit that no matching refund
  reversed, and a booking that was not cancelled. Each card and bar opens the
  Transactions tab already filtered; a country bar keeps the year the tab was
  showing.
- A **Paid by** filter at the top of the Travel tab (Everyone, Yx, Nic;
  shown once another person's charges exist) chooses whose money the tab
  reads: Yx hides the other person's card, panel and annotations; Nic shows
  only trips carrying Nic's charges, with his totals on the cards and his
  charges in the bars.
- Charges the other tracker paid for shared travel appear on the Travel tab
  too. `python scripts/import_partner_travel.py` copies every Travel row and
  every foreign-currency everyday row from the Nic tracker's published
  `app/data/transactions.json` into `manual/partner_travel.json` (Git-ignored;
  pass another path or `--paid-by` for a different tracker). The build
  publishes them as `partnerTravel` beside the transactions, never inside
  them, and the validator checks the copy against the manual file. The trip
  builder clusters them with your own rows by date and destination, so a
  visa, insurance or flight on Nic's card joins the trip it paid for; each
  card then shows "+ S$X paid by Nic · trip cost S$Y" while its own figure,
  the split and the per-day cost stay your money. A **Paid by Nic** card and
  panel list every copied charge under its trip with Nic's own owner tag, and
  charges no trip claimed sit under "Not tied to one of your trips". In the
  Transactions pane's travel views (the Travel category, a destination, or a
  focused trip) Nic's charges join the ledger with **Nic** in the Owner
  column, "Paid by Nic" under the description, a read-only drawer, and their
  own line in the summary and footer, outside your net cost; the grouped view
  leaves them out. Re-run the import after Nic's tracker rebuilds, then
  `python scripts/import_all.py`.
- Selecting the **Travel** category (or the Travel card on the Overview) adds a
  row of country/region pills with all-time counts, replaces the ordinary
  category and owner summary with a year-by-country/region breakdown, offers
  **All time** in one click, and lists **Trips** with year pills that set the
  period, so the breakdown, net cost, ledger and trips show the same year. A trip is the cluster of travel charges
  belonging to one journey: a charge matched to a Trip.com booking is anchored
  at the booking's travel dates rather than the statement date, so a flight
  charged months ahead lands on the trip it belongs to; bookingless charges use
  their statement date; charges within five days of each other form one trip;
  a bookingless charge with a known destination joins the nearest
  booking-anchored trip to the same place within 90 days; and foreign-currency
  charges dated inside a trip count as spend on the ground whatever their
  category. Each card shows total, dates, days, cost per day against the median
  of your other trips, and a flights / hotels / tickets / on-the-ground split;
  clicking one shows exactly its charges. Destination evidence comes from
  matched booking names, flight airport codes, or an explicit transaction
  location; `Singapore` in a platform's billing descriptor is not treated as
  the destination. A foreign-currency charge in a single-country currency
  (CNY, MYR, HKD, JPY and so on) is read as that country; USD and EUR are not.
  A travel charge with no evidence at all is placed by date: it joins the
  nearest booking-anchored trip it precedes by up to 90 days or falls inside,
  counts under that trip's country everywhere, and is marked "guess" in the
  ledger, "placed by date" on the trip card and in the drawer, where a saved
  destination overrules it. Any travel charge can be given a destination by
  hand in its drawer, which
  offers the nearest travel charge within a week as a starting point; the
  choice is saved as `destination` in `manual/transaction_overrides.json`, the
  build publishes it on the row and the validator checks it was applied.
  Refunds net inside the same statement year and country/region, individual
  and grouped rows show the inferred destination with a small flag drawn
  inline by `app/js/flags.js` (no downloads, so the dashboard stays offline),
  and generic Klook, KKday, Airbnb, and platform-only charges remain visibly
  grouped as **Unknown** until you set them.
- **Grab only** shows every Grab statement charge. Safely matched food rows lead
  with the stall name, while rides use friendly saved location names where
  configured. Food item lines and delivery addresses stay out of the interface;
  unmatched charges remain visible as `Wallet funding · unreconciled` rather
  than being guessed as food or transport. The drawer keeps the receipt,
  profile, payment, evidence source, and transport route where available.
  Business/Corporate matches are categorized as `Payment`; receipts with no
  profile remain ineligible for matching.
- The **Insurance** tab shows recurring premiums, coverage and policies for each
  insured person. Its compact table keeps the key fields visible; selecting a
  policy expands a concise explanation, with the full record available as a
  secondary action. Matured and lapsed policies sit in a separate archive modal.
  Scheduled annualised premiums and posted payments are deliberately separate
  because a policy may be paid by card, an imported bank account, or CPF. Card and
  bank payments reconcile against their own latest statement cutoffs; the statement
  section identifies missing and unlinked payments, groups repeated payments by
  policy, shows monthly/yearly cadence at a glance, and states whether the totals
  tally. Expanding a compact row reveals its calendar, payment history, and source
  entries. Policies checked directly against an insurer portal carry the same static
  verification badge in the register and payment list; there is no user-facing
  “mark verified” control.

Manual edits live in `manual/` and use stable content-based transaction IDs, so PDF
renames or extraction line shifts do not detach decisions (replacing a CSV source
with an equivalent PDF is the one change that still re-mints IDs). Saves and
generated-data rebuilds are atomic and roll back if validation fails.

## Accounting rules

- Interactive Brokers, SRS, fixed deposits, transfers, and card-bill payments are
  movements of money, not bank-account spending.
- Card `Payment` and `Rebates` are excluded from spending; refunds reduce totals.
- Salary deposits use the statement's explicit salary markers. In particular,
  Yx's `Inward CR - GIRO PAYNOW SALA` credits are salary, while ordinary GIRO
  credits remain transfers.
- The Transactions-page 6M/12M figures are means; overview baselines use medians.
- This clone has no Split or Games tab. Opening balances carry forward until
  replaced, and each view states its time scope.

## Common changes

- Merchant category rules: `CATEGORY_RULES` in `scripts/build_data.py`
- Salary identity and account labels: `manual/identity.json`
- Annual income and tax: `manual/salary.json` (Git-ignored). `years` rows hold
  the employment income and tax payable from each IRAS Notice of Assessment,
  filed under the income year (Year of Assessment minus one), with `growth` as
  the ratio against the previous year's income; the validator checks that
  ratio. Optional `steps` rows record monthly gross salary changes. Row order
  does not matter, the dashboard sorts by year.
- Insurance policies: `manual/insurance.json` (Git-ignored). Monthly premiums are
  annualised at 12 payments; one-off investments are excluded from recurring
  totals. Matured and lapsed records remain visible for history but are excluded
  from current coverage and premium totals. Policy drawers can also show verified
  status and dates, payment method, face/base values, riders, current valuation,
  coverage notes, the latest documents checked, an optional plain-language
  `summary`, a `verification` object with `source` and `checkedAt`, and
  `reconcileWithImportedStatements: false` for premiums paid through an account
  that is not imported into this dashboard. A family bundle may use enriched
  `components` to record each covered person, benefit and premium. A companion
  `coverageOnly: true` policy contributes coverage without double-counting its
  premium or policy count. `premiumPaidBy` labels a linked policy paid under
  someone else's bundle, while `hiddenInRegister: true` can keep a companion
  record out of the main register. Rebuild the dashboard after changing the file.
- Foodpanda order history: `manual/foodpanda_orders.json`. It is Git-ignored;
  matched pandamart orders classify the corresponding generic card charge as
  `Groceries`, while a saved transaction override remains authoritative.
- Shopee purchase history: `manual/shopee_orders.json` (also Git-ignored). The
  import retains older history and item names even when no statement link can
  be made safely. Set `statementOrderMaxHistoryIndex` at the last order covered
  by the statement window so an older same-priced order cannot be linked by
  coincidence; `statementAggregates` handles explicitly reviewed order bundles.
- Trip.com booking history: `manual/trip_bookings.json` (Git-ignored). Export the
  required periods from Trip.com's **All Bookings → Export** (each export covers
  one period; the current private file came from three covering older history,
  2025, and the past 12 months), then merge them:

  ```powershell
  python scripts/import_trip_bookings.py "My Bookings.xlsx" "My Bookings (1).xlsx" "My Bookings (2).xlsx"
  python scripts/import_all.py
  ```

  Pass the workbooks oldest first; a booking number repeated across exports keeps
  the later export's row. `--check` reports whether the JSON already matches the
  workbooks without writing. `*.xlsx` is Git-ignored, so the exports can live
  anywhere. `prepare_trip_bookings()` in `scripts/build_data.py` then validates
  booking numbers, product names, three-letter currencies, and finite amounts,
  and automatically links exact SGD amounts that pair one booking with one
  charge inside `TRIP_MATCH_WINDOW_DAYS`. Add reviewed exceptions to
  `manual/trip_booking_reconciliation.json`; every entry must name existing
  stable transaction IDs and booking numbers and explain the evidence. Only
  bookings that explain statement activity reach `app/data/`, attached with
  their booking fields and match note; the full export stays in `manual/`.
- Grab Gmail capture: `manual/grab_receipt_search_raw.json` plus any later mail
  in `manual/grab_receipt_search_supplemental.json`, parsed with `python
  scripts/import_grab_receipts.py` into `manual/grab_receipts.json`. These files
  are Git-ignored. Exact receipt references and unique direct-card amount pairs
  match first. Remaining wallet activity reconciles only when the complete
  same-day receipt and card-charge groups agree within S$1.50; mixed categories
  or profiles are never guessed.
- Grab web history: while signed in, open the Grab Help Centre article **Retrieve
  detailed Grab transaction history** (`/passenger/en-my/360038782911-How-to-find-my-Grab-transaction-history`).
  The form can preview and print a PDF for up to 300 transactions from the last
  six months, filtered by date, Personal/Business receipt type, and
  Transport/Food/Mart/Express. Split the range into smaller downloads if it
  reaches 300 rows. Saved rows live in Git-ignored
  `manual/grab_web_history.json`; the build merges them by booking code, keeps
  richer Gmail item/payment details, and excludes Business-profile bookings
  from personal finance. This is useful for filling receipt gaps, but it is
  service history rather than a GrabPay wallet
  ledger: it does not show wallet top-ups, transfers, refunds, or running
  balances, so it cannot by itself allocate every card funding charge.
- Statement-holder name, own-account labels, fixed-deposit accounts, and trusted
  counterparties: `manual/identity.json`. Optional `grabLocationAliases` entries
  map private address fragments to friendly route names such as `Home` without
  placing the addresses in tracked code. It is Git-ignored, so a fresh clone has
  none: `parse_one.py` refuses to run without `statementHolderName` (the page
  header would otherwise stay in every description), while `build_data.py`
  publishes only the account labels and trusted names the dashboard reads.
- Owner-policy preview: `python scripts/assign_unassigned.py` (add `--apply` to save); it
  refuses to run while the dashboard server is up (`--force` overrides) and
  records one audit entry for the whole batch
- Suspicious-check thresholds: documented constants at the top of
  `scripts/risk_checks.py` (card) and in `analyzeAccountTransactions` in
  `app/js/transaction-grouping.js` (bank)

## Project layout

| Path | Purpose |
|---|---|
| `statements/<year>/` | Source PDFs and older CSV exports |
| `manual/` | Identity, merchant exports, insurance, overrides, remarks, and reviews |
| `scripts/` | Parsers, data builder, validation, and local server |
| `app/` | Dashboard source and generated `app/data/` JSON |
| `tests/` | Parser, classification, API, risk, and grouping regression tests |

`statements/`, `manual/`, `app/data/`, and `tmp/` contain private or generated data
and are Git-ignored.

## Backup and restore

Back up `statements/` and `manual/`; `app/data/` can always be regenerated:

```powershell
Compress-Archive -Path statements, manual -DestinationPath "$env:USERPROFILE\Documents\UOB_backups\UOB_data_$(Get-Date -Format yyyy-MM-dd).zip"
```

To restore, extract both folders into the repository and rerun the import pipeline.

Every save also copies the `manual/` files it is about to change into
`manual/backups/` under one per-save timestamp, keeping the newest 30 copies of each
file. To undo a save, copy that timestamp's files back over the live ones and rerun
`python scripts/build_data.py`. The server creates the manual files it maintains
(owners, remarks, overrides, reviews, history) empty on startup if they are absent,
so a fresh clone runs without hand-seeding them.

## Review status

An adversarial review in September 2026 was worked through in three rounds. Done:

- Private identity (statement-holder name, own-account numbers, trusted
  counterparties) moved into git-ignored `manual/identity.json`; git history was
  rewritten and republished so no commit ever held them.
- The write API accepts only the dashboard's own origin and port, or the
  browser's `Sec-Fetch-Site` attestation, with a JSON content type, so a page on
  another localhost port cannot post edits.
- "Unassigned" clears the stable-ID tag everywhere, so merchant rules apply again.
- Trusted counterparties match by exact name; `*` opts a long stem into
  word-boundary prefix matching.
- Card and account classifiers anchor short tokens at word edges; the card
  parser keeps wrapped description tails and only recognises card section headers
  between rows.
- Saves keep rotating timestamped backups in `manual/backups/`; the server seeds
  its own empty manual files on a fresh clone; bulk assignment writes one audit
  entry and refuses to run beside a live server; the launcher reuses a hand-started
  server; bank reviews save in batches; the dashboard patches saved rows in place
  instead of refetching; heartbeats are not logged.

Still open, in rough priority:

- Whole-word classifier collisions such as GIANT LEAP or COFFEE TABLE need
  negative patterns; none occur in the current data.
- No card-testing check (several small charges from never-seen merchants on one
  day) and no cross-day velocity check for a new merchant.
- A pre-commit hook that greps staged diffs for names and account numbers.
- Identical same-day charges without a bank reference still rely on statement
  order for their occurrence number; changing their order can move a row-level
  annotation between otherwise indistinguishable charges.

## Limitations

Categories and suspicious checks are rule-based. Statements do not include receipts,
device data, precise location or time, merchant category codes, or order details, so
new and vague descriptors may still require manual review. Trip.com exports can omit
periods or represent a booking in a foreign currency while the card statement records
an SGD conversion; those rows intentionally remain generic unless the evidence is
unambiguous.
