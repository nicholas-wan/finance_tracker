# Owner decisions — do not ask again

## Two clones, one codebase (merged 6 Sep 2026)

- This repository is the upstream. Yx's clone at
  `C:\Users\nicho\Documents\yx_finances` has `origin` pointing here and
  `master` tracking `origin/master`; update her with `git pull --ff-only`
  there, never by copying files. Do not commit in her clone unless the
  change is meant for both; push it here instead.
- Clone identity is private: `manual/branding.json` (port, monogram, title,
  colours) and `manual/branding/favicon.*`. Yx's is port 3403, purple "Yx";
  this clone has none and uses the tracked defaults (3402, green "N").
- Every tab exists in the code; `tabs` in each clone's `manual/branding.json`
  decides which are shown. Nicholas: Overview, Income, Insurance, Home, Net
  worth, Games, Split, Transactions (no Travel). Yx: Overview, Income,
  Insurance, Net worth, Travel, Split, Transactions (no Home, no Games).
  Features unique to one person stay unique by that list, not by code; do
  not add a tab to the other clone's list unless asked.
- Ownership: Yx's ledger is single-owner via `"singleOwner": "Yx"` in her
  `manual/identity.json` (every charge hers, owner controls hidden). This
  clone has no such key, so owners come from stable-ID tags, legacy tags and
  merchant rules. Never hard-code a person's name in shared code; read it
  from identity or branding.
- Yx's in-page "mark verified" insurance control was dropped in the merge;
  verification lives on the policy record (`verification` object).
- The pre-commit privacy hook stops on 10-digit numbers; Yx's test fixtures
  contain three synthetic ones, so a commit touching those lines needs
  `--no-verify` after reading the finding.

## Home register (`manual/home.json`)

- Expired or not-tracked warranties are settled; never ask for their terms,
  dates, certificates or documents.
- Serial numbers are never wanted. An invoice on file is enough.
- 2024 purchases with no warranty term on any document are recorded as
  expired under an assumed 1–2 year term. Receipt-less housewarming gifts
  (air fryer, microwave, SwitchBot Hub Mini, SC10, PRISM+ arm) and Legrand
  switches are **Not tracked**.
- Manufacturer terms taken from public pages (Levoit SG 2 yr, Omnidesk
  Classic 3 yr from delivery, Neakasa 5 yr) are labelled listing-based; no
  certificate needed.
- KDK ventilation fans: part of the renovation, no receipt exists. Verified.
- Pawswing: order-page link saved, model deliberately not recorded, no
  warranty follow-up. Never load the order page — it never finishes loading.
- Neakasa M1 Plus: bought on **Yx's** Shopee account and card (23 Nov 2025,
  S$535.30), so it sits in Yx's tracker
  (`C:\Users\nicho\Documents\yx_finances`). Household items may be paid by
  either person: check Yx's tracker before asking about a missing purchase.
- Broadband: MyRepublic fibre on Yx's card (S$56.99 on the 17th); mesh is a
  TP-Link Deco M5 3-pack. The S$17.90 MYREPUBLIC LIMITED line is a different
  service.
- Home insurance: the complimentary Singlife policy (AH0012406) ended
  24 Sep 2025 and is archived. Cover is the FWD HDB Home + Fire policies in
  Yx's name, 15 Jul 2024 – 14 Jul 2029. Next renewal July 2029. The
  Downloads copy of the Singlife PDF has its password in the filename; the
  clean copy is in `manual/documents/`. Never write that password anywhere.
- DBS home loan (from digibank, 6 Sep 2026): start 1 May 2026, 26 years,
  S$251,726.63 outstanding, S$990/month all by CPF (Nicholas's OA S$495; the
  rest presumably Yx's CPF, unverified), lock-in to 1 May 2029, notice 60
  days, review 1 Feb 2029. The balance lives on the Home mortgage record and
  feeds Net worth as a derived liability. No loan debit reaches either
  tracker's statements.
- Warranty documents live in the Drive **Warranty** folder as
  `[Category] Brand Model - Document - YYYY-MM-DD.pdf`. When the owner says
  documents were uploaded, read them and fill the records.

## Net worth (`manual/net_worth.json`)

- CPF balances come from the CPF portal in the owner's Chrome (drag the tab
  into the Claude tab group). Dashboard = today's OA/SA/MA; Yearly Statement
  of Account = 31 Dec balances per year; Transaction history (12-month range,
  "See balances") = balance at the window start. Record snapshots on their
  as-at date with the page as source.
- Broker, SRS and fixed-deposit values must come from their portals. The
  statement flows are contributions, never values.
- Insurance counts at net surrender value from `manual/insurance.json`.

## Google Drive through Chrome

Drive stalls script injection after the first interaction: use `navigate` +
`wait` + `screenshot`, retry a timed-out screenshot once, and find files
with `/drive/search?q=...`. File IDs can be read from `[data-id]` right after
a navigate.
