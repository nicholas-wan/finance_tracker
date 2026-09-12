# Owner decisions — do not ask again

## Two clones, one codebase (merged 6 Sep 2026)

- This repository is the upstream. Yx's clone at
  `C:\Users\nicho\Projects\finance\yx_finances` has `origin` pointing here and
  `master` tracking `origin/master`; update her with `git pull --ff-only`
  there, never by copying files. Do not commit in her clone unless the
  change is meant for both; push it here instead.
- Clone identity is private: `manual/branding.json` (port, monogram, title,
  colours) and `manual/branding/favicon.*`. Yx's is port 3403, purple "Yx";
  this clone has none and uses the tracked defaults (3402, green "N").
- Header layout (6 Sep 2026): the logo opens Overview (no Overview tab);
  Income and Net worth are sub-tabs of **Wealth**; Games is a sub-tab of
  **Transactions**. `tabs` in each clone's `manual/branding.json` lists the
  top-level tabs shown. Nicholas: Wealth, Insurance, Home, Split,
  Transactions (no Travel). Yx: Wealth, Insurance, Travel, Transactions (no
  Home, no Split). Features unique to one person stay unique by that list,
  not by code; do not add a tab to the other clone's list unless asked.
  `setTab("income")`, `setTab("networth")` and `setTab("games")` still work
  and land on the right sub-tab.
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
  (`C:\Users\nicho\Projects\finance\yx_finances`). Household items may be paid by
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
  S$251,726.63 outstanding, S$990/month all by CPF (Nicholas's CPF S$495;
  Yx's CPF S$495, owner confirmed 7 Sep 2026), lock-in to 1 May 2029, notice 60
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
- Broker, SRS and fixed-deposit values come from their portals. Exception
  (owner decision, 12 Sep 2026): an account with no portal balance recorded
  is carried at its net contributions from the statements (cost basis) and
  marked as such in the register; Interactive Brokers is in that state. A
  recorded portal balance takes over from its own date.
- Insurance counts at net surrender value from `manual/insurance.json`.
- UOB SRS (owner, 12 Sep 2026): topped up every December by a fixed
  S$15,300. Recorded as `topUp` on the account (`{month, amount, since}`);
  the dashboard adds each year's top-up to the last known balance once
  December begins and never flags the account as stale. The statements show
  the Dec 2025 top-up only, so `since` is 2025; move it earlier only if the
  owner names an earlier first year (paid from another account).
- Reminders (owner, 12 Sep 2026): everything with a date lives in one
  "Coming up" panel on the Overview, folded to a single line unless something
  is due within 60 days. Sources: `topUp` schedules and `reminders` in the
  net-worth register (a transfer clears once the statements show it, by
  `flow` or `match`), Home register dates, annual premium anniversaries, and
  card fee waivers. Do not add a separate alert panel for a new reminder;
  add a source to `comingUpItems` in app.js. A yearly Claude scheduled task
  `srs-top-up-reminder` also fires on 1 Nov for the December top-ups.
- Mum's CPF top-up (owner, 12 Sep 2026): S$2,000 cash top-up every December
  to Mum's CPF retirement account. A gift, never an asset: it lives in
  `reminders` in `manual/net_worth.json` (matched on "CENTRAL PROVIDENT" in
  the statement description) and only ever appears as a reminder. The
  statements show Dec 2025 only, so `since` is 2025.

## Google Drive through Chrome

Drive stalls script injection after the first interaction: use `navigate` +
`wait` + `screenshot`, retry a timed-out screenshot once, and find files
with `/drive/search?q=...`. File IDs can be read from `[data-id]` right after
a navigate.

## Games (7 Sep 2026)

- 2026 purchases only: Perfect World is Neverness to Everness; G2G and
  HoYoverse are Zenless Zone Zero; Steam is Slay the Spire 2. Older years
  remain unchanged. These are exact-transaction assignments, not future
  merchant rules.
- The four fully refunded ZeusX transactions are hidden from Games, with
  their original entries retained in the main transaction ledger.
- Game title, platform, purchase type and visibility are stored as
  `gameDetails` in stable-ID transaction overrides.

- Do not add playtime, cost-per-hour, or Playing/Finished/Backlog/Dropped tracking.
  Do not show game-level personal notes; official thumbnails live locally.

- Correction: all three 2026 PlayerMatrix purchases (28 and 31 July) are
  Chaos Zero Nightmare. The four 2026 NTE* orders are Neverness to Everness,
  reclassified from Shopping to Games using exact-transaction overrides.

- G2G screenshot correction: 12 Jun 2026 is a Wuthering Waves account
  purchase; 13 and 21 Jun are Zenless Zone Zero account purchases. Order
  details and listed prices are in transaction remarks; statement charges
  remain authoritative. Evidence: manual/documents/g2g-orders-2026-06.png.

- Latest PayPal correction: 24 May 2026, S$13.35, is Neverness to Everness.
  This supersedes the earlier Wuthering Waves assignment.

- Owner confirmed all existing Kuro Games purchases are Wuthering Waves,
  including 2024 and 2025. These are assigned by transaction ID.

- Steam purchases on 22 and 28 July 2025 are Morimens (S$6.27 and S$13.19).
  The 2026 Steam purchases remain Slay the Spire 2.

- All four 2025 Xsolla / STOVEGLOBAL purchases are Chaos Zero Nightmare
  (S$38.50 total), assigned by transaction ID.

- Owner confirmed all seven existing INT*XD Entertainment purchases (June and September 2025, S$45.28 total) are Etheria Restart, assigned by transaction ID. Generic Xsolla purchases in 2023/2024 remain unassigned pending identification.
