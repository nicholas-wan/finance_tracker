# UOB finance tracker

A private local dashboard for UOB credit-card and ONE account statements. It tracks
spending, income, investments, shared expenses with Yx, game sales, balances, data
quality, and suspicious-transaction reviews. The app is plain HTML, CSS, and
JavaScript with no build step or external CDN.

## Run locally

```powershell
python scripts/serve.py
```

Open http://localhost:3402. The editable server binds only to `127.0.0.1` and is
required for saving edits and review decisions.

### Desktop or taskbar launcher

The repository includes `scripts/launch_dashboard.vbs` and
`scripts/launch_dashboard.ps1`. The desktop **Finances** shortcut runs them without
opening a PowerShell window, starts one editable server, and opens the dashboard.
Right-click the shortcut and choose **Show more options → Pin to taskbar**.

Launcher-started servers track open dashboard tabs. Closing the last tab normally
stops the server within about fifteen seconds; the grace period is long enough that
reloading the page on a busy machine does not stop the server behind it. After a
browser crash, the heartbeat timeout stops it within about two minutes. Multiple
dashboard tabs are supported. Running `python scripts/serve.py` manually remains
persistent until `Ctrl+C`; the launcher reuses a healthy manually started server and
only opens the dashboard against it, rather than replacing it.

For a view-only dashboard:

```powershell
python -m http.server 3402 --directory app
```

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

- Card summaries show net cost after refunds, category and owner breakdowns, and
  6M/12M comparisons. **Group purchases** combines normalized merchants without
  merging the source rows.
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
- **Grab only** shows every Grab statement charge. Safely matched food rows lead
  with the stall name, while rides use friendly saved location names where
  configured. Food item lines and delivery addresses stay out of the interface;
  unmatched charges remain visible as `Wallet funding · unreconciled` rather
  than being guessed as food or transport. The drawer keeps the receipt,
  profile, payment, evidence source, and transport route where available.
  Business/Corporate matches are categorized as `Payment`; receipts with no
  profile remain ineligible for matching.

Manual edits live in `manual/` and use stable content-based transaction IDs, so PDF
renames or extraction line shifts do not detach decisions (replacing a CSV source
with an equivalent PDF is the one change that still re-mints IDs). Saves and
generated-data rebuilds are atomic and roll back if validation fails.

## Accounting rules

- Interactive Brokers, SRS, fixed deposits, transfers, and card-bill payments are
  movements of money, not bank-account spending.
- Card `Payment` and `Rebates` are excluded from spending; refunds reduce totals.
- Salary figures are gross manual values and are not inferred from deposits.
- The Transactions-page 6M/12M figures are means; overview baselines use medians.
- The Split tab and Overview share one Yx settlement calculation. Opening balances
  carry forward until replaced, and each view states its time scope.
- `Yx share` is half of `Shared` plus transactions assigned directly to `Yx`.
- Game sales stay on the Games page because they do not pass through statements.

## Common changes

- Merchant category rules: `CATEGORY_RULES` in `scripts/build_data.py`
- Game seller rules: `GAME_RULES` in `scripts/build_data.py`
- Salary history: `manual/salary.json`
- Game-account sales: `manual/game_sales.json`
- Foodpanda order history: `manual/foodpanda_orders.json`. It is Git-ignored;
  matched pandamart orders classify the corresponding generic card charge as
  `Groceries`, while a saved transaction override remains authoritative.
- Shopee purchase history: `manual/shopee_orders.json` (also Git-ignored). The
  import retains older history and item names even when no statement link can
  be made safely.
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
| `manual/` | Owners, overrides, remarks, reviews, settlements, salary, game sales, and `identity.json` |
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
new and vague descriptors may still require manual review.
