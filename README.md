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

For a view-only dashboard:

```powershell
python -m http.server 3402 --directory app
```

## Import statements

Place PDFs in `statements/<year>/`, then run:

```powershell
python scripts/parse_cc.py
python scripts/parse_one.py
python scripts/build_data.py
python scripts/validate_data.py
python -m unittest discover -s tests
node --test tests/*.js
```

Parsing fails closed: an unreadable row, unreconciled card section, contradictory
amount, or broken balance chain aborts the run without replacing generated data.
Validation checks stable IDs, dates, provenance, account continuity, manual
references, and source-to-dashboard totals. Audit history is exempt from reference
checks because history may outlive the rows it describes. Use `validate_data.py
--strict` to also fail on unresolved categories, ownership, provenance, or
suspicious checks.

Statement months come from PDF contents. Importing a second source for an existing
month is rejected, while legitimate identical transactions within a statement are
preserved.

## Dashboard

The Transactions page supports period, search, category/flow, owner, direction, and
review-status filters.

- Card summaries show net cost after refunds, category and owner breakdowns, and
  6M/12M comparisons. **Group purchases** combines normalized merchants without
  merging the source rows.
- Bank summaries show opening and closing balances, reconciliation, money movement,
  non-transfer spending, and 6M/12M spending averages. Bank rows can be grouped by
  counterparty; transfers and internal movements can be hidden.
- **Review transaction** opens the exact bank row needing attention. Review prompts
  explain their reason and are not fraud verdicts. Decisions are saved and audited.
  Recognition is per signal, not per transaction: a new kind of alert on an already
  recognized transaction re-surfaces. Card checks net refunds against charges within
  a week per merchant before flagging.
- Opening any transaction shows its original statement description and provenance.
  Card transactions also support display-name, category, owner, and remark edits.
- **History** shows recorded manual changes.

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
- Owner-policy preview: `python scripts/assign_unassigned.py` (add `--apply` to save)
- Suspicious-check thresholds: documented constants at the top of
  `scripts/risk_checks.py` (card) and in `analyzeAccountTransactions` in
  `app/js/transaction-grouping.js` (bank)

## Project layout

| Path | Purpose |
|---|---|
| `statements/<year>/` | Source PDFs and older CSV exports |
| `manual/` | Owners, overrides, remarks, reviews, settlements, salary, and game sales |
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

## Limitations

Categories and suspicious checks are rule-based. Statements do not include receipts,
device data, precise location or time, merchant category codes, or order details, so
new and vague descriptors may still require manual review.
