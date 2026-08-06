# UOB finance tracker

A private local dashboard for UOB credit-card and ONE account statements. It tracks
income, card spending, investments, shared expenses with Yx, game sales, data quality,
and suspicious-transaction review.

The app is plain HTML, CSS, and JavaScript. It has no build step or external CDN, but
it must be served over HTTP because it loads generated JSON files at runtime.

## Run locally

```powershell
python scripts/serve.py
```

Open http://localhost:3402. The editable server binds only to `127.0.0.1`.

To run the dashboard in view-only mode:

```powershell
python -m http.server 3402 --directory app
```

View-only mode cannot save owners, categories, remarks, transaction details, or review
decisions.

## Import new statements

Place PDFs in `statements/<year>/`, then run:

```powershell
python scripts/parse_cc.py
python scripts/parse_one.py
python scripts/build_data.py
python scripts/validate_data.py
python -m unittest discover -s tests -v
node --test tests/test_transaction_grouping.js
```

Parsing fails closed: a row that cannot be fully parsed, a card section that does not
reconcile, or a printed amount that contradicts its balance movement aborts the run,
and no output JSON is written. The card parser reconciles parsed rows against statement
balances. The account parser checks each printed amount against its balance movement.
`validate_data.py` verifies stable IDs, dates, provenance, balance chains, all
hand-entered references (owner tags, overrides, remarks, risk reviews), and
source-to-dashboard totals. Audit history is exempt from reference checks because
history may legitimately outlive the rows it describes. Use `--strict` when unresolved categories, ownership,
provenance, or suspicious checks should also fail validation.

Statements are authoritative. Rows are never deduplicated: identical charges can be
real separate transactions. A second source file for an already imported statement
month is rejected to prevent accidental double ingestion.

## Using the dashboard

The Transactions page supports search, period, category, and owner filters. Its summary
shows net cost after refunds with category and owner breakdowns.

**Group purchases** combines normalized merchant descriptions within the current
filters. Category and owner remain separate, and refunds reduce the grouped amount.
This changes only the display; the underlying transaction rows are never merged.

Open a transaction to edit its display name, category, owner, remark, or suspicious
review. The original statement description and provenance remain unchanged. The
**History** button shows edits made after audit history was enabled.

Edits are written to `manual/` by stable transaction ID. IDs are hashed from
transaction content and source type (date, description, amount, card, direction,
card-vs-account), never from the source filename or page position, so renaming a PDF
or re-extracting it cannot detach manual decisions. Replacing a CSV source with an
equivalent PDF still changes IDs. The server writes atomically, rebuilds the dashboard, validates the
result, and restores the previous files if the operation fails.

## Important accounting rules

- Withdrawals to Interactive Brokers, SRS, and fixed deposits are wealth transfers,
  not spending.
- `Payment` and `Rebates` are excluded from card spending.
- Refunds reduce both transaction summaries and grouped merchant totals.
- Salary figures are gross manual values and cannot be derived from bank deposits.
- Overview and insight baselines use the median so annual insurance and other large
  periodic charges do not distort comparisons. The Transactions-page 6M and 12M
  comparison is a mean by design, which is why it is labelled `avg`.
- The Yx settlement position is one calculation shared by the Split tab and the
  Overview insight. The insight stops at the selected month, the Split tab covers the
  whole year, and each states its own time scope. An opening balance carries forward
  through later years that have no opening entry of their own.
- Home-page `Spent` includes every non-wealth outflow, while the spending KPI covers
  card purchases.
- `Yx share` is half of `Shared` plus transactions assigned directly to `Yx`. Settlement
  entries and opening balances determine who currently owes whom.
- Game sales remain isolated to the Games page because they do not pass through the
  statements.
- Statement month comes from the PDF contents, not the filename.
- Suspicious checks are review prompts, not fraud verdicts. Recognizing a transaction is
  a local decision, not proof that it was authorized.

## Project layout

| Path | Purpose |
|---|---|
| `statements/<year>/` | Source PDFs and older CSV exports |
| `manual/` | Owners, overrides, remarks, reviews, settlements, salary, and game sales |
| `scripts/` | Parsers, data builder, validation, and local server |
| `app/` | Dashboard source; `app/data/` contains generated JSON |
| `tests/` | Parser, classification, API, risk, and grouping regression tests |

`statements/`, `manual/`, and `app/data/` contain private or generated financial data
and are Git-ignored.

## Common maintenance

- Categorize a new merchant by adding a rule to `CATEGORY_RULES` in
  `scripts/build_data.py`, or override one transaction from its detail panel.
- Add a game seller to `GAME_RULES` in `scripts/build_data.py`.
- Add a game-account sale to `manual/game_sales.json`.
- Add a salary step to `manual/salary.json`.
- Preview the standing owner policy with `python scripts/assign_unassigned.py`; add
  `--apply` to save, rebuild, and validate it.

## Backup and restore

Code is versioned in git; financial data (`statements/`, `manual/`, `app/data/`,
`tmp/`) is never committed. When taking a snapshot, commit and push the code, then zip
the data alongside it:

```powershell
Compress-Archive -Path statements, manual -DestinationPath "$env:USERPROFILE\Documents\UOB_backups\UOB_data_$(Get-Date -Format yyyy-MM-dd).zip"
```

To restore on a fresh machine: clone the repo, extract the latest
`UOB_data_*.zip` into the repo root so `statements/` and `manual/` are back in place,
then run the import pipeline (see "Import new statements") to regenerate `app/data/`.
`app/data/` never needs backing up; it is fully derived from statements plus manual.

## Known limitations

- Transaction checks only know the statement date, description, card, amount, refunds,
  history, and printed foreign amount. They cannot see receipts, device data, precise
  location, transaction time, merchant category code, or order details.
- Merchant categories are rule-based and may require manual review when a new or vague
  descriptor appears.
